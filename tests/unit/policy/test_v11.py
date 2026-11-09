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

"""Policy Engine v1.1: fail-closed targets, complete approval digest, mandatory audit, isolation."""

from pathlib import Path

import pytest

from artemis.drivers.base import ScreenData
from artemis.policy import PolicyEngine, PolicyViolation
from artemis.policy.approvals import ApprovalStore
from artemis.policy.audit import AuditLog
from artemis.policy.guard import PolicyGuardedDriver
from artemis.policy.model import Action, Decision, PolicyContext
from artemis.policy.rules import evaluate
from policy_helpers import RecordingDriver, approve_later, element, run

A, R = Decision.ALLOW, Decision.REQUIRE_APPROVAL


def tap(**over):
    base = {"op": "tap", "device_id": "d", "params": {"x": 1, "y": 1}}
    base.update(over)
    return Action(**base)


# ---- P0-01: a target we could not identify is never "plain navigation" ------------------------


def test_unreadable_screen_requires_approval():
    action = tap(foreground_package="com.android.settings", ui_known=False)
    verdict = evaluate(action)
    assert verdict.decision is R and verdict.policy_id.endswith("ui.context_unavailable")


def test_unknown_foreground_app_requires_approval():
    assert evaluate(tap(foreground_package=None, ui_known=True)).decision is R


def test_unlabeled_control_in_messaging_app_requires_approval():
    assert evaluate(tap(foreground_package="com.whatsapp", ui_known=True)).decision is R


def test_unlabeled_control_in_neutral_app_is_allowed():
    assert evaluate(tap(foreground_package="com.android.settings", ui_known=True)).decision is A


def test_swipes_do_not_need_a_target():
    swipe = Action(
        "swipe",
        "d",
        {"x": 5, "y": 900, "end_x": 5, "end_y": 100},
        foreground_package="com.android.chrome",
    )
    assert evaluate(swipe).decision is A


def test_enter_with_unknown_app_requires_approval():
    enter = Action("press_key", "d", {"key": "enter"}, foreground_package=None)
    assert evaluate(enter).decision is R


class BlindScreen(RecordingDriver):
    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        raise RuntimeError("uiautomator down")


def test_tap_with_unreadable_screen_is_not_executed_without_approval(engine):
    phone = BlindScreen(package="com.android.settings")
    guard = PolicyGuardedDriver(phone, engine)
    with pytest.raises(PolicyViolation):
        run(guard.tap(5, 5))
    assert phone.effects == []


def test_tap_with_unreadable_screen_runs_once_a_human_approves(engine):
    phone = BlindScreen(package="com.android.settings")
    guard = PolicyGuardedDriver(phone, engine)
    thread, _ = approve_later(engine.store)
    run(guard.tap(5, 5))
    thread.join()
    assert phone.effects == [("tap", (5, 5))]


# ---- P0-02: the approval covers the whole context -------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"context": PolicyContext(origin="unknown", requester="mallory")},
        {"foreground_package": "com.other.app"},
        {"target_label": "Delete account"},
        {"ui_known": True},
    ],
)
def test_digest_changes_with_every_context_field(change):
    base = tap(foreground_package="com.whatsapp", target_label="Send")
    assert (
        base.digest()
        != tap(**{"foreground_package": "com.whatsapp", "target_label": "Send", **change}).digest()
    )


def test_approval_for_one_screen_is_refused_on_another(tmp_path):
    store = ApprovalStore(tmp_path / "x.db")
    shown = tap(foreground_package="com.whatsapp", target_label="Send", ui_known=True)
    aid = store.request(shown)
    store.decide(aid, True, "alice")
    other = tap(foreground_package="com.bank.app", target_label="Send", ui_known=True)
    assert store.consume(aid, other) is False
    assert store.consume(aid, shown) is True


class ChangingScreen(RecordingDriver):
    """Shows 'Send' on the first read, something else under the same coordinates afterwards."""

    def __init__(self):
        super().__init__(package="com.whatsapp")
        self.reads = 0

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        self.reads += 1
        label = "Send" if self.reads == 1 else "Delete all chats"
        return ScreenData(
            screenshot_bytes=b"",
            screenshot_base64="",
            ui_elements=[element(label, (0, 0, 200, 200))],
        )


def test_screen_change_between_approval_and_execution_blocks_the_action(engine):
    phone = ChangingScreen()
    guard = PolicyGuardedDriver(phone, engine)
    thread, result = approve_later(engine.store)
    with pytest.raises(PolicyViolation, match="changed"):
        run(guard.tap(100, 100))
    thread.join()
    assert result["ok"] is True  # the human did approve the first screen
    assert phone.effects == []  # but the tap never happened
    assert any(e.get("detail") == "context_changed_after_approval" for e in engine.audit.read())


# ---- P1-04: no action without its trace in production ---------------------------------------


class BrokenAudit(AuditLog):
    def _write(self, entry):
        raise OSError("read-only filesystem")


def _engine_with_broken_audit(tmp_path: Path) -> PolicyEngine:
    return PolicyEngine(ApprovalStore(tmp_path / "e.db"), BrokenAudit(tmp_path / "a.jsonl"))


def test_allow_still_runs_in_development_when_audit_is_down(policy_dir):
    eng = _engine_with_broken_audit(policy_dir)
    run(eng.enforce(Action("press_key", "d", {"key": "back"})))


def test_allow_is_refused_in_production_when_audit_is_down(policy_dir, monkeypatch):
    monkeypatch.setenv("ARTEMIS_ENV", "production")
    eng = _engine_with_broken_audit(policy_dir)
    with pytest.raises(PolicyViolation, match="audit"):
        run(eng.enforce(Action("press_key", "d", {"key": "back"})))


def test_audit_required_flag_works_outside_production(policy_dir, monkeypatch):
    monkeypatch.setenv("ARTEMIS_POLICY_AUDIT_REQUIRED", "1")
    eng = _engine_with_broken_audit(policy_dir)
    with pytest.raises(PolicyViolation):
        run(eng.enforce(Action("press_key", "d", {"key": "back"})))


# ---- P1-03: the guard does not blindly delegate ---------------------------------------------


def test_unknown_attributes_are_refused_not_delegated(guarded):
    for name in ("serial", "adb_shell", "some_new_raw_method", "_inner"):
        with pytest.raises(AttributeError):
            getattr(guarded, name)


def test_explicit_read_only_passthrough_still_works(engine):
    phone = RecordingDriver()
    phone.action_history = [{"op": "tap"}]
    assert PolicyGuardedDriver(phone, engine).action_history == [{"op": "tap"}]


def test_nothing_outside_the_guard_touches_its_inner_driver():
    root = Path(__file__).resolve().parents[3]
    offenders = []
    for path in (root / "artemis").rglob("*.py"):
        if path.name == "guard.py" and path.parent.name == "policy":
            continue
        text = path.read_text(encoding="utf-8")
        if "_PolicyGuardedDriver__inner" in text or "guard._inner" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


# ---- identity / origin propagation (worker boundary goes through the environment) ---------------


def test_worker_context_comes_from_the_environment(monkeypatch):
    from artemis.policy import current_context, set_context

    set_context.__globals__["_current"].set(None)
    monkeypatch.delenv("ARTEMIS_POLICY_ORIGIN", raising=False)
    monkeypatch.setenv("ARTEMIS_TASK_INGRESS", "frontend")
    monkeypatch.setenv("ARTEMIS_SESSION_ID", "sess-42")
    monkeypatch.setenv("ARTEMIS_POLICY_REQUESTER", "api-bearer")
    ctx = current_context()
    assert (ctx.origin, ctx.session_id, ctx.requester) == ("task:frontend", "sess-42", "api-bearer")


def test_ensure_context_only_fills_a_missing_origin(monkeypatch):
    from artemis.policy import current_context, ensure_context, set_context

    set_context.__globals__["_current"].set(None)
    for var in ("ARTEMIS_POLICY_ORIGIN", "ARTEMIS_TASK_INGRESS"):
        monkeypatch.delenv(var, raising=False)
    ensure_context("mcp")
    assert current_context().origin == "mcp"
    set_context("voice", session_id="s")
    ensure_context("mcp")  # must not overwrite a richer, explicit context
    assert current_context().origin == "voice"


def test_session_change_invalidates_an_approval(tmp_path):
    store = ApprovalStore(tmp_path / "s.db")
    mine = tap(
        foreground_package="com.whatsapp", ui_known=True,
        context=PolicyContext(origin="task:frontend", session_id="s1", requester="api-bearer"),
    )  # fmt: skip
    aid = store.request(mine)
    store.decide(aid, True, "alice")
    theirs = tap(
        foreground_package="com.whatsapp", ui_known=True,
        context=PolicyContext(origin="task:frontend", session_id="s2", requester="api-bearer"),
    )  # fmt: skip
    assert store.consume(aid, theirs) is False


# ---- RT-02: indirect actions -------------------------------------------------------------------


@pytest.mark.parametrize(
    "package", ["com.whatsapp", "com.google.android.gm", "com.amazon.mShop.android.shopping"]
)
def test_horizontal_swipe_in_messaging_or_shopping_apps_requires_approval(package):
    swipe = Action(
        "swipe", "d", {"x": 900, "y": 800, "end_x": 100, "end_y": 810}, foreground_package=package
    )
    assert evaluate(swipe).decision is R


def test_shell_swipe_is_judged_like_a_driver_swipe():
    horizontal = Action(
        "shell", "d", {"command": "input swipe 900 800 100 800"}, foreground_package="com.whatsapp"
    )
    vertical = Action(
        "shell", "d", {"command": "input swipe 500 1500 500 500"}, foreground_package="com.whatsapp"
    )
    assert evaluate(horizontal).decision is R and evaluate(vertical).decision is A


def test_strict_ui_mode_covers_unlabeled_controls_and_swipes_everywhere(monkeypatch):
    plain = tap(foreground_package="com.example.notes", ui_known=True)
    assert evaluate(plain).decision is A  # default: residual risk, documented
    monkeypatch.setenv("ARTEMIS_POLICY_STRICT_UI", "1")
    assert evaluate(plain).decision is R
    swipe = Action(
        "swipe",
        "d",
        {"x": 900, "y": 800, "end_x": 100, "end_y": 800},
        foreground_package="com.example.notes",
    )
    assert evaluate(swipe).decision is R
