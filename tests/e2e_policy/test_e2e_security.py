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

"""E2E security scenarios through the real daemon: replay, screen change, concurrency, audit, RT-*.

Same boundary as ``test_e2e_entrypoints``: real daemon/queue/CLI/policy, simulated phone and model.
"""

import os
from pathlib import Path

from harness import API_TOKEN, element, http, wait_for
from entries import approver_headers, decide, pending
import pytest

from artemis.policy.approvals import ApprovalStore


def taps(env) -> list[dict]:
    return [e for e in env.effects() if e["effect"] == "tap"]


def send_screen(env, label: str = "Send") -> None:
    env.set_screen("com.whatsapp", [element(label)])


# ---- replay ---------------------------------------------------------------------------------


def test_replay_of_a_spent_approval_is_refused_and_a_repeat_needs_a_new_one(entries, env):
    send_screen(env)
    entries.api("scenario:send_message")
    first = wait_for(lambda: pending(entries.base), timeout=30)[0]["id"]
    assert decide(entries.base, first)[0] == 200
    assert decide(entries.base, first)[0] == 409  # same approval cannot be granted twice
    assert decide(entries.base, first, "deny")[0] == 409  # nor flipped
    wait_for(lambda: len(taps(env)) == 1)

    # The very same action again: the spent approval must not be accepted a second time.
    entries.api("scenario:send_message")
    second = wait_for(lambda: pending(entries.base), timeout=30)
    assert second and second[0]["id"] != first
    assert len(taps(env)) == 1  # still only the first tap
    assert decide(entries.base, first)[0] == 409
    wait_for(lambda: len(env.results()) == 2, timeout=30)  # expires unapproved
    assert len(taps(env)) == 1


def test_a_different_session_cannot_reuse_an_approval(entries, env):
    """An approval granted for session A's action is worthless for session B's identical action."""
    send_screen(env)
    a = entries.cli_standalone("scenario:send_message", session="session-A")
    ida = wait_for(lambda: pending(entries.base), timeout=30)[0]
    assert ida["session_id"] == "session-A"
    b = entries.cli_standalone("scenario:send_message", session="session-B")
    wait_for(lambda: len(pending(entries.base)) == 2, timeout=30)
    assert decide(entries.base, ida["id"])[0] == 200  # approve only A
    a.wait(timeout=30)
    assert len(taps(env)) == 1
    b.wait(timeout=30)  # B's own request expires unapproved
    assert len(taps(env)) == 1
    sessions = {r["results"][0]["context"][1]: r for r in env.results()}
    assert sessions["session-A"]["results"][-1]["ok"] is True
    assert sessions["session-B"]["results"][-1]["ok"] is False


# ---- screen change between approval and execution ---------------------------------------------


def test_screen_change_after_approval_voids_it(entries, env):
    send_screen(env, "Send")
    entries.api("scenario:send_message")
    item = wait_for(lambda: pending(entries.base), timeout=30)[0]
    send_screen(env, "Delete all conversations")  # the UI changes under the same coordinates
    assert decide(entries.base, item["id"])[0] == 200
    wait_for(lambda: env.results(), timeout=30)
    assert taps(env) == []
    assert "context_changed" in env.results()[-1]["results"][-1]["policy"]
    assert any(a.get("detail") == "context_changed_after_approval" for a in env.audit())


def test_the_agents_api_token_cannot_approve(entries, env):
    send_screen(env)
    entries.api("scenario:send_message")
    item = wait_for(lambda: pending(entries.base), timeout=30)[0]
    status, _ = http(
        "POST", f"{entries.base}/api/policy/approvals/{item['id']}/approve", API_TOKEN,
        headers={"X-Approver-Token": API_TOKEN},
    )  # fmt: skip
    assert status == 403
    status, _ = http("POST", f"{entries.base}/api/policy/approvals/{item['id']}/approve", API_TOKEN)
    assert status == 401
    wait_for(lambda: env.results(), timeout=30)
    assert taps(env) == []


# ---- RT-01: concurrent sessions ---------------------------------------------------------------


def test_concurrent_sessions_keep_their_own_identity_and_approvals(entries, env):
    send_screen(env)
    procs = {
        f"s{i}": entries.cli_standalone("scenario:send_message", session=f"s{i}") for i in range(4)
    }
    wait_for(lambda: len(pending(entries.base)) == 4, timeout=45)
    by_session = {p["session_id"]: p["id"] for p in pending(entries.base)}
    assert set(by_session) == set(procs)  # four distinct requests, one per session
    for session in ("s1", "s3"):
        assert decide(entries.base, by_session[session])[0] == 200
    for proc in procs.values():
        proc.wait(timeout=40)
    outcome = {r["results"][0]["context"][1]: r["results"][-1]["ok"] for r in env.results()}
    assert outcome == {"s0": False, "s1": True, "s2": False, "s3": True}
    assert len(taps(env)) == 2
    contexts = {tuple(r["results"][0]["context"][:2]) for r in env.results()}
    assert contexts == {("task:cli", f"s{i}") for i in range(4)}


# ---- RT-02: indirect actions ------------------------------------------------------------------


def test_horizontal_swipe_in_a_messaging_app_needs_approval(entries, env):
    env.set_screen("com.google.android.gm", [])
    entries.api("scenario:swipe_away")
    wait_for(lambda: pending(entries.base), timeout=30)
    assert [e for e in env.effects() if e["effect"] == "swipe"] == []
    wait_for(lambda: env.results(), timeout=30)
    assert [e for e in env.effects() if e["effect"] == "swipe"] == []


def test_unlabeled_button_in_a_messaging_app_needs_approval(entries, env):
    env.set_screen("org.thoughtcrime.securesms", [element("", (0, 0, 200, 200))])
    entries.api("scenario:send_message")
    wait_for(lambda: pending(entries.base), timeout=30)
    assert taps(env) == []
    wait_for(lambda: env.results(), timeout=30)
    assert taps(env) == []


def test_unlabeled_button_in_an_unclassified_app_is_a_known_residual_risk(entries, env):
    """Documented gap: without STRICT_UI an unlabeled control in a neutral app passes."""
    env.set_screen("com.example.notes", [element("", (0, 0, 200, 200))])
    entries.api("scenario:send_message")
    wait_for(lambda: env.results(), timeout=30)
    assert len(taps(env)) == 1


def test_strict_ui_closes_the_unlabeled_button_gap(entries, env, monkeypatch):
    monkeypatch.setenv("ARTEMIS_POLICY_STRICT_UI", "1")
    env.set_screen("com.example.notes", [element("", (0, 0, 200, 200))])
    entries.api("scenario:send_message")
    wait_for(lambda: pending(entries.base), timeout=30)
    assert taps(env) == []
    wait_for(lambda: env.results(), timeout=30)
    assert taps(env) == []


def test_benign_actions_before_a_sensitive_one_do_not_carry_authorization(entries, env):
    send_screen(env)
    entries.api("scenario:benign_then_sensitive")
    wait_for(lambda: pending(entries.base), timeout=30)
    wait_for(lambda: env.results(), timeout=30)
    effects = [e["effect"] for e in env.effects()]
    assert effects == ["swipe"]  # the scroll ran; the sensitive tap and what follows did not


# ---- audit unavailable -----------------------------------------------------------------------


def _break_audit(monkeypatch, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setenv("ARTEMIS_POLICY_DIR", str(blocker / "policy"))  # cannot be created


def test_production_runs_nothing_when_the_journal_is_unavailable(
    entries, env, monkeypatch, tmp_path
):
    _break_audit(monkeypatch, tmp_path)
    monkeypatch.setenv("ARTEMIS_ENV", "production")
    entries.api("scenario:open_settings")  # even a harmless, allowed action
    wait_for(lambda: env.results(), timeout=30)
    assert env.effects() == []
    assert "audit" in env.results()[-1]["results"][-1]["policy"]


def test_development_keeps_allowed_actions_available_when_the_journal_is_down(
    entries, env, monkeypatch, tmp_path
):
    _break_audit(monkeypatch, tmp_path)
    entries.api("scenario:open_settings")
    wait_for(lambda: env.results(), timeout=30)
    assert [e["effect"] for e in env.effects()] == ["launch_app"]
