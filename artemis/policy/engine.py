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

"""The policy engine: evaluate, ask for approval when needed, audit, and fail closed."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from artemis.policy import rules
from artemis.policy.approvals import DEFAULT_TTL, ApprovalStore, TooManyPending
from artemis.policy.audit import AuditLog
from artemis.policy.model import Action, Decision, PolicyViolation, Verdict


logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.environ.get("ARTEMIS_ENV", "").lower() == "production"


def audit_required() -> bool:
    """In production (or ``ARTEMIS_POLICY_AUDIT_REQUIRED=1``) no action runs without its trace."""
    return _is_production() or os.environ.get("ARTEMIS_POLICY_AUDIT_REQUIRED") == "1"


def current_mode() -> str:
    """``enforce`` (default) or ``audit`` (log only; development, ignored in production)."""
    mode = os.environ.get("ARTEMIS_POLICY_MODE", "enforce").lower()
    if mode == "audit" and not _is_production():
        return "audit"
    return "enforce"


class PolicyEngine:
    def __init__(
        self,
        store: ApprovalStore | None = None,
        audit: AuditLog | None = None,
        approval_timeout: float | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self._store = store
        self._audit = audit
        self.approval_timeout = (
            approval_timeout
            if approval_timeout is not None
            else float(os.environ.get("ARTEMIS_APPROVAL_TIMEOUT", DEFAULT_TTL))
        )
        self.poll_interval = poll_interval

    @property
    def store(self) -> ApprovalStore:
        if self._store is None:
            self._store = ApprovalStore()
        return self._store

    @property
    def audit(self) -> AuditLog:
        if self._audit is None:
            self._audit = AuditLog()
        return self._audit

    def evaluate(self, action: Action) -> Verdict:
        try:
            return rules.evaluate(action)
        except Exception as exc:  # noqa: BLE001 - a broken rule must never become ALLOW
            return Verdict(
                Decision.UNKNOWN,
                f"{rules.POLICY_VERSION}/engine.error",
                f"policy error: {type(exc).__name__}",
            )

    async def enforce(self, action: Action) -> str:
        """Return a trace id if the action may proceed; raise :class:`PolicyViolation` if not."""
        trace_id, _ = await self.enforce_with_status(action)
        return trace_id

    async def enforce_with_status(self, action: Action) -> tuple[str, bool]:
        """Like :meth:`enforce`, also telling whether a human approval was consumed."""
        trace_id = uuid.uuid4().hex[:16]
        verdict = self.evaluate(action)
        mode = current_mode()
        self._safe_audit_decision(trace_id, action, verdict, mode)
        if verdict.decision is Decision.ALLOW:
            return trace_id, False
        if mode == "audit":
            self._safe_audit_outcome(trace_id, "audit_only_not_blocked", verdict.decision.value)
            return trace_id, False
        if verdict.decision is Decision.REQUIRE_APPROVAL:
            await self._await_approval(trace_id, action, verdict)
            return trace_id, True
        self._safe_audit_outcome(trace_id, "blocked", verdict.decision.value)
        raise PolicyViolation(verdict, trace_id)

    def reject_stale_approval(self, trace_id: str, action: Action) -> None:
        """The approved screen no longer matches reality: refuse, the approval is already spent."""
        self._safe_audit_outcome(trace_id, "blocked", "context_changed_after_approval")
        raise PolicyViolation(
            Verdict(
                Decision.DENY,
                f"{rules.POLICY_VERSION}/approval.context_changed",
                "the screen changed between approval and execution",
            ),
            trace_id,
        )

    async def _await_approval(self, trace_id: str, action: Action, verdict: Verdict) -> None:
        try:
            approval_id = await asyncio.to_thread(self.store.request, action, self.approval_timeout)
        except TooManyPending:
            self._safe_audit_outcome(trace_id, "blocked", "too_many_pending_approvals")
            raise PolicyViolation(
                Verdict(
                    Decision.DENY,
                    f"{rules.POLICY_VERSION}/approval.flood",
                    "too many pending approvals",
                ),
                trace_id,
            ) from None
        except Exception as exc:  # noqa: BLE001 - store failure => fail closed
            self._safe_audit_outcome(trace_id, "blocked", "approval_store_error")
            raise PolicyViolation(
                Verdict(
                    Decision.DENY,
                    f"{rules.POLICY_VERSION}/approval.store",
                    f"approval store unavailable: {type(exc).__name__}",
                ),
                trace_id,
            ) from exc
        self._safe_audit_outcome(trace_id, "approval_requested", approval_id)
        deadline = asyncio.get_running_loop().time() + self.approval_timeout
        while True:
            status = await asyncio.to_thread(self.store.status, approval_id)
            if status == "approved":
                if await asyncio.to_thread(self.store.consume, approval_id, action):
                    self._safe_audit_outcome(trace_id, "approved_and_consumed", approval_id)
                    return
                status = "consumed_elsewhere"
            if status in {"denied", "expired", "consumed", "consumed_elsewhere", "unknown"}:
                self._safe_audit_outcome(trace_id, "not_approved", status)
                raise PolicyViolation(
                    Verdict(
                        Decision.DENY, f"{rules.POLICY_VERSION}/approval.{status}",
                        f"approval {status}", *verdict.effects,
                    ),
                    trace_id,
                )  # fmt: skip
            if asyncio.get_running_loop().time() >= deadline:
                self._safe_audit_outcome(trace_id, "not_approved", "timeout")
                raise PolicyViolation(
                    Verdict(
                        Decision.DENY,
                        f"{rules.POLICY_VERSION}/approval.timeout",
                        "approval timed out",
                        *verdict.effects,
                    ),
                    trace_id,
                )
            await asyncio.sleep(self.poll_interval)

    def record_outcome(self, trace_id: str, ok: bool, detail: str = "") -> None:
        self._safe_audit_outcome(trace_id, "executed" if ok else "failed", detail)

    # The audit trail must not be able to break the phone, nor be skipped silently.
    def _safe_audit_decision(self, trace_id, action, verdict, mode) -> None:  # noqa: ANN001
        try:
            self.audit.decision(trace_id, action, verdict, mode)
        except Exception:  # noqa: BLE001
            if mode == "enforce" and (verdict.decision is not Decision.ALLOW or audit_required()):
                raise PolicyViolation(
                    Verdict(
                        Decision.DENY,
                        f"{rules.POLICY_VERSION}/audit.unavailable",
                        "audit trail unavailable",
                    ),
                    trace_id,
                ) from None

    def _safe_audit_outcome(self, trace_id: str, result: str, detail: str = "") -> None:
        try:
            self.audit.outcome(trace_id, result, detail)
        except Exception as exc:  # noqa: BLE001 - an outcome record must not break the phone
            logger.warning("policy audit outcome not recorded: %s", type(exc).__name__)


_engine: PolicyEngine | None = None


def get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine


def set_engine(engine: PolicyEngine | None) -> None:
    """Replace the process-wide engine (tests)."""
    global _engine
    _engine = engine
