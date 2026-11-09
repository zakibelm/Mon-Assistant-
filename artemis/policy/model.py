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

"""Policy model: structured actions, effects and verdicts."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from typing import Any


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"
    UNKNOWN = "UNKNOWN"

    @property
    def severity(self) -> int:
        return {"ALLOW": 0, "REQUIRE_APPROVAL": 1, "UNKNOWN": 2, "DENY": 3}[self.value]


class Effect(str, Enum):
    READ = "read"
    NAVIGATE = "navigate"
    INPUT = "input"
    MODIFY = "modify"
    COMMUNICATE = "communicate"
    DESTROY = "destroy"
    FINANCE = "finance"
    SYSTEM = "system"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    policy_id: str
    reason: str
    effects: tuple[Effect, ...] = ()

    @staticmethod
    def worst(verdicts: list[Verdict]) -> Verdict:
        return max(verdicts, key=lambda v: v.decision.severity)


@dataclass(frozen=True)
class PolicyContext:
    """Who is asking and where: recorded in the audit trail and bound to approvals."""

    origin: str = "unknown"  # voice | api | mcp | cli | console | unknown
    session_id: str = ""
    requester: str = ""


_current: ContextVar[PolicyContext | None] = ContextVar("artemis_policy_context", default=None)


def set_context(origin: str, session_id: str = "", requester: str = "") -> None:
    _current.set(PolicyContext(origin=origin, session_id=session_id, requester=requester))


def current_context() -> PolicyContext:
    ctx = _current.get()
    if ctx is not None:
        return ctx
    return PolicyContext(
        origin=os.environ.get("ARTEMIS_POLICY_ORIGIN", "unknown"),
        session_id=os.environ.get("ARTEMIS_SESSION_ID", ""),
    )


@dataclass
class Action:
    """One concrete effect about to reach the phone."""

    op: str  # tap | long_press | swipe | input_text | press_key | launch_app | stop_app | shell
    device_id: str
    params: dict[str, Any] = field(default_factory=dict)
    foreground_package: str | None = None
    target_label: str | None = None  # visible label at the tapped point, if known
    context: PolicyContext = field(default_factory=PolicyContext)
    # (x, y) -> visible label, pre-resolved by the guard for `input tap` inside shell lines
    labels: dict[tuple[int, int], str] = field(default_factory=dict)

    def digest(self) -> str:
        """Stable identity of THIS action for THIS device/session/origin (used by approvals)."""
        canonical = json.dumps(
            {
                "op": self.op,
                "device": self.device_id,
                "params": self.params,
                "session": self.context.session_id,
                "origin": self.context.origin,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def summary(self) -> str:
        """Short human-readable description for the approver (no typed text, no secrets)."""
        parts = [self.op]
        if self.op == "shell":
            parts.append(str(self.params.get("command", ""))[:120])
        elif self.op in {"tap", "long_press"}:
            parts.append(f"at ({self.params.get('x')},{self.params.get('y')})")
        elif self.op == "input_text":
            parts.append(f"({self.params.get('length', 0)} characters)")
        elif "package" in self.params or "key" in self.params:
            parts.append(str(self.params.get("package") or self.params.get("key")))
        if self.target_label:
            parts.append(f"on '{self.target_label[:40]}'")
        if self.foreground_package:
            parts.append(f"in {self.foreground_package}")
        return " ".join(parts)


class PolicyViolation(RuntimeError):
    """Raised instead of performing an action the policy does not allow."""

    def __init__(self, verdict: Verdict, trace_id: str = "") -> None:
        self.verdict = verdict
        self.trace_id = trace_id
        super().__init__(
            f"policy_{verdict.decision.value.lower()}: {verdict.reason} "
            f"[{verdict.policy_id}] (trace {trace_id}). Do not retry this action; "
            "tell the user it needs their authorization."
        )
