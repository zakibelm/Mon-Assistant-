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

"""Approver endpoints for the policy engine.

Deciding needs a credential the agent never has: ``X-Approver-Token`` must match an entry of
``ARTEMIS_APPROVER_TOKENS`` (``name:token,name2:token2``) and must differ from the API token.
The approver's name is recorded as the identity on the decision.
"""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException

from artemis.policy.approvals import ApprovalStore
from artemis.policy.audit import AuditLog

router = APIRouter()


def _approvers() -> dict[str, str]:
    pairs = {}
    for entry in os.environ.get("ARTEMIS_APPROVER_TOKENS", "").split(","):
        name, _, token = entry.partition(":")
        if name.strip() and token.strip():
            pairs[name.strip()] = token.strip()
    return pairs


def identify_approver(token: str | None) -> str:
    approvers = _approvers()
    api_token = os.environ.get("ARTEMIS_API_TOKEN", "")
    if not approvers:
        raise HTTPException(status_code=503, detail="approvals_not_configured")
    if not token:
        raise HTTPException(status_code=401, detail="approver_token_required")
    if api_token and hmac.compare_digest(token.encode(), api_token.encode()):
        raise HTTPException(status_code=403, detail="approver_token_must_differ_from_api_token")
    for name, expected in approvers.items():
        if hmac.compare_digest(token.encode(), expected.encode()):
            return name
    raise HTTPException(status_code=403, detail="invalid_approver_token")


@router.get("/api/policy/approvals")
async def list_approvals(x_approver_token: str | None = Header(default=None)):
    identify_approver(x_approver_token)
    return {"pending": ApprovalStore().list_pending()}


@router.post("/api/policy/approvals/{approval_id}/{verdict}")
async def decide_approval(
    approval_id: str, verdict: str, x_approver_token: str | None = Header(default=None)
):
    if verdict not in {"approve", "deny"}:
        raise HTTPException(status_code=404, detail="unknown_verdict")
    approver = identify_approver(x_approver_token)
    if not ApprovalStore().decide(approval_id, verdict == "approve", approver):
        raise HTTPException(status_code=409, detail="not_pending")
    AuditLog().outcome(approval_id, f"approver_{verdict}", approver)
    return {"status": "approved" if verdict == "approve" else "denied", "by": approver}


@router.get("/api/policy/audit")
async def read_audit(n: int = 50, x_approver_token: str | None = Header(default=None)):
    identify_approver(x_approver_token)
    return {"entries": AuditLog().read()[-max(1, min(n, 500)) :]}
