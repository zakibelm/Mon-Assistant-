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

"""Approvals: bound to the exact action, expiring, single-use, atomic; plus engine failure modes."""

import concurrent.futures as cf
import time

import pytest

from artemis.policy import PolicyEngine, PolicyViolation
from artemis.policy.approvals import MAX_PENDING, ApprovalStore, TooManyPending
from artemis.policy.audit import AuditLog
from artemis.policy.engine import current_mode
from artemis.policy.model import Action, Decision, PolicyContext, Verdict

from policy_helpers import approve_later, run


def sensitive(**over):
    base = {"op": "shell", "device_id": "dev", "params": {"command": "pm clear com.x"}}
    base.update(over)
    return Action(**base)


@pytest.fixture
def store(policy_dir):
    return ApprovalStore(policy_dir / "t.db")


def test_nobody_can_consume_a_pending_request(store):
    a = sensitive()
    aid = store.request(a)
    assert store.consume(aid, a) is False


def test_approved_request_is_single_use(store):
    a = sensitive()
    aid = store.request(a)
    assert store.decide(aid, True, "alice")
    assert store.consume(aid, a) is True
    assert store.consume(aid, a) is False  # replay
    assert store.status(aid) == "consumed"


def test_approval_is_bound_to_the_exact_action(store):
    a = sensitive()
    aid = store.request(a)
    store.decide(aid, True, "alice")
    assert store.consume(aid, sensitive(params={"command": "pm clear com.other"})) is False
    assert store.consume(aid, sensitive(device_id="other-device")) is False
    other = sensitive(context=PolicyContext(origin="api", session_id="other-session"))
    assert store.consume(aid, other) is False
    assert store.consume(aid, a) is True  # the right action still works


def test_expired_approval_cannot_be_used(store):
    a = sensitive()
    aid = store.request(a, ttl=0.05)
    store.decide(aid, True, "alice")
    time.sleep(0.1)
    assert store.consume(aid, a) is False
    assert store.status(aid) == "expired"


def test_cannot_approve_after_expiry_or_twice(store):
    aid = store.request(sensitive(), ttl=0.05)
    time.sleep(0.1)
    assert store.decide(aid, True, "alice") is False
    aid2 = store.request(sensitive(params={"command": "pm clear b"}))
    assert store.decide(aid2, False, "alice") is True
    assert store.decide(aid2, True, "mallory") is False  # a denial cannot be flipped


def test_unknown_id_is_refused(store):
    assert store.status("nope") == "unknown"
    assert store.decide("nope", True, "alice") is False
    assert store.consume("nope", sensitive()) is False


def test_duplicate_pending_requests_are_reused(store):
    assert store.request(sensitive()) == store.request(sensitive())
    assert len(store.list_pending()) == 1


def test_pending_requests_are_capped(store):
    for i in range(MAX_PENDING):
        store.request(sensitive(params={"command": f"pm clear p{i}"}))
    with pytest.raises(TooManyPending):
        store.request(sensitive(params={"command": "pm clear overflow"}))


def test_concurrent_consume_has_exactly_one_winner(store):
    a = sensitive()
    aid = store.request(a)
    store.decide(aid, True, "alice")
    with cf.ThreadPoolExecutor(16) as pool:
        wins = list(pool.map(lambda _: store.consume(aid, a), range(16)))
    assert wins.count(True) == 1


def test_concurrent_engines_one_approval_one_execution(engine):
    """Two agents ask for the same action; one human approval must authorize only one of them."""
    import asyncio

    a = sensitive()

    async def both():
        return await asyncio.gather(engine.enforce(a), engine.enforce(a), return_exceptions=True)

    thread, _ = approve_later(engine.store)
    results = run(both())
    thread.join()
    ok = [r for r in results if isinstance(r, str)]
    bad = [r for r in results if isinstance(r, PolicyViolation)]
    assert len(ok) == 1 and len(bad) == 1


def test_replay_needs_a_fresh_approval(engine):
    a = sensitive()
    thread, _ = approve_later(engine.store)
    run(engine.enforce(a))
    thread.join()
    with pytest.raises(PolicyViolation):  # same action again, nobody approved it
        run(engine.enforce(a))


def test_agent_side_cannot_self_approve(engine):
    """The engine exposes no path from 'request' to 'approved' without store.decide()."""
    a = sensitive()
    with pytest.raises(PolicyViolation, match="expired|timed out"):
        run(engine.enforce(a))
    assert engine.store.list_pending() == []  # expired, nothing left to approve


# ---- failure modes: everything fails closed -----------------------------------------------


def test_rule_exception_becomes_unknown_and_is_blocked(engine, monkeypatch):
    from artemis.policy import rules

    def boom(_):
        raise ValueError("bug")

    monkeypatch.setattr(rules, "evaluate", boom)
    verdict = engine.evaluate(sensitive())
    assert verdict.decision is Decision.UNKNOWN and "engine.error" in verdict.policy_id
    with pytest.raises(PolicyViolation):
        run(engine.enforce(sensitive()))


def test_approval_store_failure_blocks(policy_dir):
    class Broken(ApprovalStore):
        def request(self, *a, **k):
            raise OSError("disk full")

    eng = PolicyEngine(
        Broken(policy_dir / "b.db"), AuditLog(policy_dir / "x.jsonl"),
        approval_timeout=0.2, poll_interval=0.02,
    )  # fmt: skip
    with pytest.raises(PolicyViolation, match="store unavailable"):
        run(eng.enforce(sensitive()))


def test_audit_failure_blocks_non_allow_decisions(policy_dir):
    class BrokenAudit(AuditLog):
        def _write(self, entry):
            raise OSError("read-only fs")

    eng = PolicyEngine(ApprovalStore(policy_dir / "c.db"), BrokenAudit(policy_dir / "y.jsonl"))
    with pytest.raises(PolicyViolation, match="audit"):
        run(eng.enforce(Action("shell", "d", {"command": "su"})))


# ---- modes ------------------------------------------------------------------------------


def test_audit_mode_logs_but_does_not_block_outside_production(engine, monkeypatch):
    monkeypatch.setenv("ARTEMIS_POLICY_MODE", "audit")
    run(engine.enforce(Action("shell", "d", {"command": "su -c id"})))
    assert engine.audit.read()[-1]["result"] == "audit_only_not_blocked"


def test_production_forces_enforcement(engine, monkeypatch):
    monkeypatch.setenv("ARTEMIS_POLICY_MODE", "audit")
    monkeypatch.setenv("ARTEMIS_ENV", "production")
    assert current_mode() == "enforce"
    with pytest.raises(PolicyViolation):
        run(engine.enforce(Action("shell", "d", {"command": "su -c id"})))


# ---- audit content ----------------------------------------------------------------------


def test_audit_links_decision_and_outcome_without_leaking_content(engine, phone):
    from artemis.policy.guard import PolicyGuardedDriver

    secret = "hunter2-very-secret"
    guard = PolicyGuardedDriver(phone, engine)
    run(guard.input_text(secret))
    run(guard.execute_shell("getprop ro.x"))
    raw = engine.audit.path.read_text()
    assert secret not in raw
    assert "hunter2" not in raw
    entries = engine.audit.read()
    decisions = [e for e in entries if e["event"] == "decision"]
    assert decisions and all(
        {"trace_id", "policy_id", "decision", "reason"} <= e.keys() for e in decisions
    )
    trace = decisions[0]["trace_id"]
    assert any(e["event"] == "outcome" and e["trace_id"] == trace for e in entries)


def test_audit_file_is_private(engine, phone):
    import stat

    from artemis.policy.guard import PolicyGuardedDriver

    run(PolicyGuardedDriver(phone, engine).press_key("back"))
    assert stat.S_IMODE(engine.audit.path.stat().st_mode) == 0o600


def test_verdict_severity_ordering():
    worst = Verdict.worst(
        [Verdict(Decision.ALLOW, "a", ""), Verdict(Decision.DENY, "b", ""),
         Verdict(Decision.UNKNOWN, "c", "")]
    )  # fmt: skip
    assert worst.decision is Decision.DENY
