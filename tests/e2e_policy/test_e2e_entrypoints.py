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

"""Decisive test: the same action through the voice server, /api/run, MCP and the CLI.

Real: FastAPI daemon + middleware + /api/run + task queue + approver API, the voice HTTP server
and its ArtemisClient, the MCP ``mobile_run_task`` tool, the ``artemis`` CLI, the driver factory,
the policy guard, engine, approval store and audit log.
Simulated: the phone (``FakePhone``) and the model (scripted plans). Nothing here touches a real
Android device.
"""

from harness import element, wait_for
from entries import ORIGINS, decide, pending
import pytest

ENTRIES = ["api", "voice", "mcp", "cli", "cli_standalone"]


def run_and_wait(entries, env, entry: str, goal: str, approve: bool | None = None) -> dict:
    """Submit, optionally approve through the real approver API, and return the worker's result."""
    before = len(env.results())
    entries.submit(entry, goal)
    if approve is not None:
        wait_for(lambda: pending(entries.base), timeout=30)
        if approve:
            for item in pending(entries.base):
                assert decide(entries.base, item["id"])[0] == 200
    wait_for(lambda: len(env.results()) > before, timeout=45)
    return env.results()[-1]


def taps(env) -> list[dict]:
    return [e for e in env.effects() if e["effect"] == "tap"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_allowed_action_runs_and_context_is_conserved(entries, env, entry):
    result = run_and_wait(entries, env, entry, "scenario:open_settings")
    assert [e["effect"] for e in env.effects()] == ["launch_app"]
    origin, session, requester = result["results"][0]["context"]
    assert origin == ORIGINS[entry] and session
    if entry != "cli_standalone":  # queued tasks carry the caller class set by the queue
        assert requester == "api-bearer"
    decision = [a for a in env.audit() if a["event"] == "decision"][-1]
    assert (decision["origin"], decision["session"]) == (origin, session)


@pytest.mark.parametrize("entry", ENTRIES)
def test_communication_waits_for_an_approver_then_runs_once(entries, env, entry):
    env.set_screen("com.whatsapp", [element("Send")])
    before = len(env.results())
    entries.submit(entry, "scenario:send_message")
    wait_for(lambda: pending(entries.base), timeout=30)
    assert taps(env) == []  # nothing happened while the request is pending
    result = run_and_wait_after(entries, env, before)
    assert len(taps(env)) == 1 and result["results"][-1]["ok"] is True


def run_and_wait_after(entries, env, before: int) -> dict:
    for item in pending(entries.base):
        assert decide(entries.base, item["id"])[0] == 200
    wait_for(lambda: len(env.results()) > before, timeout=45)
    return env.results()[-1]


@pytest.mark.parametrize("entry", ENTRIES)
def test_unapproved_communication_never_executes(entries, env, entry):
    env.set_screen("com.whatsapp", [element("Send")])
    result = run_and_wait(entries, env, entry, "scenario:send_message")  # nobody approves
    assert taps(env) == []
    assert result["results"][-1]["ok"] is False
    assert "approval" in result["results"][-1]["policy"]


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize(
    ("goal", "screen", "policy_fragment"),
    [
        ("scenario:wipe_sdcard", ("com.android.settings", []), "shell"),
        ("scenario:var_expansion", ("com.android.settings", []), "shell"),
        (
            "scenario:pay",
            ("com.android.chrome", [element("Pay now", (250, 0, 500, 200))]),
            "finance",
        ),
    ],
)
def test_denied_and_unknown_actions_never_execute(
    entries, env, entry, goal, screen, policy_fragment
):
    env.set_screen(*screen)
    result = run_and_wait(entries, env, entry, goal)
    assert [e for e in env.effects() if e["effect"] in {"tap", "shell"}] == []
    assert result["results"][-1]["ok"] is False
    assert policy_fragment in result["results"][-1]["policy"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_every_decision_is_audited_with_its_trace(entries, env, entry):
    env.set_screen("com.android.settings")
    run_and_wait(entries, env, entry, "scenario:wipe_sdcard")
    audit = env.audit()
    decisions = [a for a in audit if a["event"] == "decision"]
    assert decisions and decisions[-1]["decision"] == "DENY"
    trace = decisions[-1]["trace_id"]
    assert any(a["event"] == "outcome" and a["trace_id"] == trace for a in audit)
