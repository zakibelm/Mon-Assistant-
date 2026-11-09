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

"""Fixtures: an isolated policy directory, an engine on it, and a guarded recording driver."""

import pytest

from artemis.policy import PolicyEngine, set_context, set_engine
from artemis.policy.approvals import ApprovalStore
from artemis.policy.audit import AuditLog
from artemis.policy.guard import PolicyGuardedDriver
from policy_helpers import RecordingDriver


@pytest.fixture
def policy_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_POLICY_DIR", str(tmp_path))
    for var in ("ARTEMIS_ENV", "ARTEMIS_POLICY_MODE", "ARTEMIS_POLICY_FINANCE"):
        monkeypatch.delenv(var, raising=False)
    set_context("unknown")
    return tmp_path


@pytest.fixture
def engine(policy_dir):
    eng = PolicyEngine(
        ApprovalStore(policy_dir / "a.db"),
        AuditLog(policy_dir / "audit.jsonl"),
        approval_timeout=1.0,
        poll_interval=0.02,
    )
    set_engine(eng)
    yield eng
    set_engine(None)


@pytest.fixture
def phone():
    return RecordingDriver()


@pytest.fixture
def guarded(engine, phone):
    return PolicyGuardedDriver(phone, engine)
