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

"""Append-only audit trail for policy decisions.

One JSON line per decision and one per outcome, joined by ``trace_id``. Typed text, secrets
and screen content are never written: only lengths, digests, the command verb, and the
policy that decided.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from artemis.policy.model import Action, Verdict

_lock = threading.Lock()


def policy_dir() -> Path:
    configured = os.environ.get("ARTEMIS_POLICY_DIR")
    if configured:
        path = Path(configured)
    else:
        from artemis.config.paths import get_app_dir

        path = get_app_dir() / "policy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_params(action: Action) -> dict[str, Any]:
    """Audit-safe view of an action's parameters."""
    p = action.params
    if action.op == "shell":
        command = str(p.get("command", ""))
        verbs = []
        for part in command.replace("&&", ";").replace("||", ";").replace("|", ";").split(";"):
            tokens = part.split()
            if tokens:
                verbs.append(os.path.basename(tokens[0])[:30])
        return {
            "verbs": verbs[:8],
            "length": len(command),
            "sha256": hashlib.sha256(command.encode()).hexdigest()[:16],
        }
    if action.op == "input_text":
        return {"length": p.get("length", 0)}  # never the text, never a hash of it
    return {k: v for k, v in p.items() if k in {"x", "y", "key", "package", "direction"}}


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (policy_dir() / "audit.jsonl")

    def _write(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, sort_keys=True, default=str)
        with _lock:
            fresh = not self.path.exists()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if fresh:
                try:
                    self.path.chmod(0o600)
                except OSError:
                    pass

    def decision(self, trace_id: str, action: Action, verdict: Verdict, mode: str) -> None:
        self._write(
            {
                "ts": round(time.time(), 3),
                "event": "decision",
                "trace_id": trace_id,
                "op": action.op,
                "device": action.device_id,
                "session": action.context.session_id,
                "origin": action.context.origin,
                "package": action.foreground_package,
                "label_seen": bool(action.target_label),
                "params": safe_params(action),
                "decision": verdict.decision.value,
                "policy_id": verdict.policy_id,
                "reason": verdict.reason,
                "effects": [e.value for e in verdict.effects],
                "mode": mode,
            }
        )

    def outcome(self, trace_id: str, result: str, detail: str = "") -> None:
        self._write(
            {
                "ts": round(time.time(), 3),
                "event": "outcome",
                "trace_id": trace_id,
                "result": result,
                "detail": detail[:160],
            }
        )

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line
        ]
