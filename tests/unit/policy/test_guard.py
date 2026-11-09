# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Guard: nothing reaches the phone unless the policy lets it through."""

import pytest

from artemis.policy import PolicyViolation, set_context
from artemis.policy.approvals import ApprovalStore
from artemis.policy.guard import PolicyGuardedDriver, label_at

from policy_helpers import RecordingDriver, approve_later, element, run


def test_allowed_actions_reach_the_phone(guarded, phone):
    run(guarded.tap(10, 10))
    run(guarded.press_key("back"))
    run(guarded.launch_app("com.android.chrome"))
    run(guarded.execute_shell("getprop ro.build.version.sdk"))
    assert [e[0] for e in phone.effects] == ["tap", "press_key", "launch_app", "shell"]


@pytest.mark.parametrize(
    "call",
    [
        lambda g: g.execute_shell("rm -rf /sdcard"),
        lambda g: g.execute_shell("su -c id"),
        lambda g: g.execute_shell("echo $(id)"),
        lambda g: g.launch_app("com.paypal.android.p2pmobile"),
        lambda g: g.press_key("weird-key"),
    ],
)
def test_denied_or_unknown_never_reaches_the_phone(guarded, phone, call):
    with pytest.raises(PolicyViolation):
        run(call(guarded))
    assert phone.effects == []


def test_violation_message_tells_the_agent_not_to_retry(guarded):
    with pytest.raises(PolicyViolation, match="Do not retry"):
        run(guarded.execute_shell("reboot"))


def test_label_under_the_finger_decides(engine):
    phone = RecordingDriver(
        package="com.whatsapp",
        elements=[
            element("Send", (0, 0, 200, 200)),
            element("Pay now", (300, 0, 500, 200)),
            element("Back", (600, 0, 800, 200)),
        ],
    )
    guard = PolicyGuardedDriver(phone, engine)
    run(guard.tap(700, 100))  # harmless button
    assert phone.effects == [("tap", (700, 100))]
    with pytest.raises(PolicyViolation):  # finance label => DENY, no approval path
        run(guard.tap(400, 100))
    assert len(phone.effects) == 1


def test_sensitive_tap_waits_for_out_of_band_approval(engine):
    phone = RecordingDriver(package="com.whatsapp", elements=[element("Send", (0, 0, 200, 200))])
    guard = PolicyGuardedDriver(phone, engine)
    thread, result = approve_later(engine.store)
    run(guard.tap(100, 100))
    thread.join()
    assert result["ok"] is True
    assert phone.effects == [("tap", (100, 100))]


def test_sensitive_tap_refused_when_the_human_says_no(engine):
    phone = RecordingDriver(package="com.whatsapp", elements=[element("Send", (0, 0, 200, 200))])
    guard = PolicyGuardedDriver(phone, engine)
    thread, _ = approve_later(engine.store, approve=False)
    with pytest.raises(PolicyViolation, match="denied"):
        run(guard.tap(100, 100))
    thread.join()
    assert phone.effects == []


def test_no_approval_means_timeout_and_no_effect(engine):
    phone = RecordingDriver(package="com.whatsapp", elements=[element("Send", (0, 0, 200, 200))])
    guard = PolicyGuardedDriver(phone, engine)
    with pytest.raises(PolicyViolation, match="expired|timed out"):
        run(guard.tap(100, 100))
    assert phone.effects == []


def test_shell_input_tap_is_label_resolved(engine):
    phone = RecordingDriver(package="com.whatsapp", elements=[element("Pay now", (0, 0, 200, 200))])
    guard = PolicyGuardedDriver(phone, engine)
    with pytest.raises(PolicyViolation):
        run(guard.execute_shell("input tap 100 100"))
    assert phone.effects == []


def test_check_shell_authorizes_without_executing(guarded, phone):
    run(guarded.check_shell("getprop ro.x"))
    assert phone.effects == []
    with pytest.raises(PolicyViolation):
        run(guarded.check_shell("rm -rf /sdcard"))


def test_multi_action_sequence_stops_at_the_sensitive_step(engine):
    phone = RecordingDriver(package="com.whatsapp", elements=[element("Send", (0, 0, 200, 200))])
    guard = PolicyGuardedDriver(phone, engine)
    run(guard.press_key("back"))
    run(guard.swipe(1, 1, 2, 2))
    with pytest.raises(PolicyViolation):
        run(guard.tap(100, 100))  # needs approval, none given
    assert [e[0] for e in phone.effects] == ["press_key", "swipe"]


def test_destructive_command_split_across_calls_is_still_caught(guarded, phone):
    """Each call is judged on its own; no sequence of harmless calls assembles a destructive one."""
    run(guarded.execute_shell("cd /sdcard"))  # relative state is not trusted either
    with pytest.raises(PolicyViolation):
        run(guarded.execute_shell("rm -rf *"))
    assert all("rm" not in str(e) for e in phone.effects)


def test_helpers_built_on_tap_stay_behind_the_guard(engine):
    phone = RecordingDriver(package="com.whatsapp", elements=[element("Pay now", (0, 0, 500, 500))])
    guard = PolicyGuardedDriver(phone, engine)
    with pytest.raises(PolicyViolation):
        run(guard.tap_normalized(0.1, 0.1))
    assert phone.effects == []


@pytest.mark.parametrize("name", ["device", "_device", "adb_client", "ui_adb_client", "adb"])
def test_raw_handles_are_not_reachable_through_the_guard(guarded, name):
    with pytest.raises(AttributeError):
        getattr(guarded, name)


def test_inner_failure_is_audited_and_propagated(engine):
    class Boom(RecordingDriver):
        async def tap(self, *a, **k):
            raise RuntimeError("adb gone")

    guard = PolicyGuardedDriver(Boom(), engine)
    with pytest.raises(RuntimeError):
        run(guard.tap(1, 1))
    assert engine.audit.read()[-1]["result"] == "failed"


def test_label_at_picks_the_smallest_clickable_box():
    els = [element("Big", (0, 0, 1000, 1000)), element("Small", (10, 10, 50, 50))]
    assert "Small" in label_at(els, 20, 20)


def test_origin_is_recorded_in_the_audit(engine, phone):
    set_context("voice", session_id="s1", requester="zaki")
    guard = PolicyGuardedDriver(phone, engine)
    run(guard.press_key("back"))
    assert engine.audit.read()[0]["origin"] == "voice"


def test_store_is_shared_between_instances(policy_dir):
    a, b = ApprovalStore(policy_dir / "s.db"), ApprovalStore(policy_dir / "s.db")
    assert a.list_pending() == b.list_pending() == []
