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

"""Human approvals: out-of-band, exact-action, expiring, atomic, single-use.

Requests live in a small SQLite store shared by the daemon and its worker processes.
The agent can *ask* (``request``) but cannot *grant*: granting needs the approver channel
(local ``python -m artemis.policy`` on the host, or the HTTP endpoint with a separate
approver credential). Every state change is one atomic ``UPDATE ... WHERE`` so two
concurrent callers can never both consume the same approval.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import time
import uuid

from artemis.policy.audit import policy_dir
from artemis.policy.model import Action

DEFAULT_TTL = 120.0
MAX_PENDING = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    op TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    decided_by TEXT,
    decided_at REAL,
    consumed_at REAL
)
"""


class TooManyPending(RuntimeError):
    pass


class ApprovalStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (policy_dir() / "approvals.sqlite3")
        with self._db() as db:
            db.execute(_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # -- requester side ---------------------------------------------------------------
    def request(self, action: Action, ttl: float = DEFAULT_TTL) -> str:
        now = time.time()
        digest = action.digest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE approvals SET status='expired' WHERE status IN ('pending','approved') AND expires_at<=?",
                (now,),
            )
            same = db.execute(
                "SELECT id FROM approvals WHERE digest=? AND status='pending' AND expires_at>?",
                (digest, now),
            ).fetchone()
            if same:
                db.execute("COMMIT")
                return same["id"]
            pending = db.execute(
                "SELECT COUNT(*) AS n FROM approvals WHERE status='pending'"
            ).fetchone()["n"]
            if pending >= MAX_PENDING:
                db.execute("ROLLBACK")
                raise TooManyPending("too many pending approval requests")
            approval_id = uuid.uuid4().hex[:12]
            db.execute(
                "INSERT INTO approvals (id,digest,device_id,session_id,origin,op,summary,status,requested_at,expires_at)"
                " VALUES (?,?,?,?,?,?,?,'pending',?,?)",
                (
                    approval_id, digest, action.device_id, action.context.session_id,
                    action.context.origin, action.op, action.summary(), now, now + ttl,
                ),
            )  # fmt: skip
            db.execute("COMMIT")
            return approval_id

    def status(self, approval_id: str) -> str:
        with self._db() as db:
            row = db.execute(
                "SELECT status, expires_at FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
        if row is None:
            return "unknown"
        if row["status"] in {"pending", "approved"} and row["expires_at"] <= time.time():
            return "expired"
        return row["status"]

    def consume(self, approval_id: str, action: Action) -> bool:
        """Atomically spend an approval for EXACTLY this action. Exactly one caller can win."""
        now = time.time()
        with self._db() as db:
            cur = db.execute(
                "UPDATE approvals SET status='consumed', consumed_at=? "
                "WHERE id=? AND status='approved' AND digest=? AND device_id=? "
                "AND session_id=? AND expires_at>?",
                (
                    now,
                    approval_id,
                    action.digest(),
                    action.device_id,
                    action.context.session_id,
                    now,
                ),
            )
            return cur.rowcount == 1

    # -- approver side ----------------------------------------------------------------
    def list_pending(self) -> list[dict]:
        now = time.time()
        with self._db() as db:
            rows = db.execute(
                "SELECT id,op,summary,device_id,session_id,origin,requested_at,expires_at "
                "FROM approvals WHERE status='pending' AND expires_at>? ORDER BY requested_at",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def decide(self, approval_id: str, approve: bool, approver: str) -> bool:
        now = time.time()
        with self._db() as db:
            cur = db.execute(
                "UPDATE approvals SET status=?, decided_by=?, decided_at=? "
                "WHERE id=? AND status='pending' AND expires_at>?",
                ("approved" if approve else "denied", approver, now, approval_id, now),
            )
            return cur.rowcount == 1
