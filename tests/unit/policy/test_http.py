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

"""Out-of-band approver endpoints, and the same-policy-for-every-entry-point check."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps.admin_console.routers import policy as policy_router
from artemis.policy import PolicyEngine, PolicyViolation, set_context
from artemis.policy.approvals import ApprovalStore
from artemis.policy.audit import AuditLog
from artemis.policy.guard import PolicyGuardedDriver
from artemis.policy.model import Action
from policy_helpers import RecordingDriver, element, run

APPROVER = "approver-secret-1"
API = "api-secret-1"


@pytest.fixture
def client(policy_dir, monkeypatch):
    monkeypatch.setenv("ARTEMIS_APPROVER_TOKENS", f"alice:{APPROVER}")
    monkeypatch.setenv("ARTEMIS_API_TOKEN", API)
    app = FastAPI()
    app.include_router(policy_router.router)
    return TestClient(app)


def hdr(token):
    return {"X-Approver-Token": token}


def pending_action(policy_dir):
    store = ApprovalStore()
    return store, store.request(Action("shell", "dev", {"command": "pm clear com.x"}))


def test_not_configured_means_nobody_can_approve(policy_dir, monkeypatch):
    monkeypatch.delenv("ARTEMIS_APPROVER_TOKENS", raising=False)
    app = FastAPI()
    app.include_router(policy_router.router)
    resp = TestClient(app).get("/api/policy/approvals", headers=hdr("anything"))
    assert resp.status_code == 503


def test_missing_and_wrong_tokens_are_refused(client, policy_dir):
    _, aid = pending_action(policy_dir)
    assert client.post(f"/api/policy/approvals/{aid}/approve").status_code == 401
    assert (
        client.post(f"/api/policy/approvals/{aid}/approve", headers=hdr("nope")).status_code == 403
    )
    assert ApprovalStore().status(aid) == "pending"


def test_the_agents_api_token_cannot_approve(client, policy_dir):
    """The token an agent/voice client holds must not double as an approval credential."""
    _, aid = pending_action(policy_dir)
    resp = client.post(f"/api/policy/approvals/{aid}/approve", headers=hdr(API))
    assert resp.status_code == 403
    assert ApprovalStore().status(aid) == "pending"


def test_approve_then_replay_is_refused(client, policy_dir):
    _, aid = pending_action(policy_dir)
    ok = client.post(f"/api/policy/approvals/{aid}/approve", headers=hdr(APPROVER))
    assert ok.status_code == 200 and ok.json()["by"] == "alice"
    again = client.post(f"/api/policy/approvals/{aid}/approve", headers=hdr(APPROVER))
    assert again.status_code == 409
    flip = client.post(f"/api/policy/approvals/{aid}/deny", headers=hdr(APPROVER))
    assert flip.status_code == 409


def test_unknown_verdict_and_unknown_id(client):
    assert client.post("/api/policy/approvals/x/maybe", headers=hdr(APPROVER)).status_code == 404
    assert (
        client.post("/api/policy/approvals/zzz/approve", headers=hdr(APPROVER)).status_code == 409
    )


def test_listing_and_audit_need_the_approver_token(client, policy_dir):
    pending_action(policy_dir)
    assert client.get("/api/policy/approvals").status_code == 401
    assert client.get("/api/policy/audit").status_code == 401
    listed = client.get("/api/policy/approvals", headers=hdr(APPROVER)).json()["pending"]
    assert len(listed) == 1 and "summary" in listed[0]


def test_http_approval_unblocks_a_waiting_action(client, policy_dir):
    """Full loop: agent blocks on a sensitive tap, a separate credential approves, tap runs once."""
    import threading
    import time

    # the router uses the default store location, so the engine must share it
    engine = PolicyEngine(
        ApprovalStore(), AuditLog(policy_dir / "audit.jsonl"),
        approval_timeout=3.0, poll_interval=0.02,
    )  # fmt: skip

    phone = RecordingDriver(package="com.whatsapp", elements=[element("Send", (0, 0, 200, 200))])
    guard = PolicyGuardedDriver(phone, engine)

    def approver():
        deadline = time.time() + 3
        while time.time() < deadline:
            pending = client.get("/api/policy/approvals", headers=hdr(APPROVER)).json()["pending"]
            if pending:
                client.post(
                    f"/api/policy/approvals/{pending[0]['id']}/approve", headers=hdr(APPROVER)
                )
                return
            time.sleep(0.02)

    thread = threading.Thread(target=approver, daemon=True)
    thread.start()
    run(guard.tap(100, 100))
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert phone.effects == [("tap", (100, 100))]


# ---- same policy for every entry point --------------------------------------------------------


@pytest.mark.parametrize("origin", ["voice", "api", "mcp", "cli", "console", "unknown"])
def test_every_entry_point_is_subject_to_the_same_policy(engine, origin):
    """Origin is recorded for the audit but never relaxes a decision.

    This exercises the shared choke point (the guarded driver) with each origin label. It does
    NOT drive the real voice page / HTTP route / MCP server / CLI end to end.
    """
    set_context(origin, session_id=f"s-{origin}")
    phone = RecordingDriver(
        package="com.android.chrome", elements=[element("Pay now", (0, 0, 9, 9))]
    )
    guard = PolicyGuardedDriver(phone, engine)
    with pytest.raises(PolicyViolation):
        run(guard.tap(1, 1))  # finance label
    with pytest.raises(PolicyViolation):
        run(guard.execute_shell("rm -rf /sdcard"))
    with pytest.raises(PolicyViolation):
        run(guard.execute_shell("echo $(id)"))
    assert phone.effects == []
    assert {e["origin"] for e in engine.audit.read() if e["event"] == "decision"} == {origin}
