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

"""Central policy layer: every effect on the phone is decided here, before it happens."""

from artemis.policy.engine import PolicyEngine, current_mode, get_engine, set_engine
from artemis.policy.model import (
    Action,
    Decision,
    Effect,
    PolicyContext,
    PolicyViolation,
    Verdict,
    current_context,
    set_context,
)

__all__ = [
    "Action",
    "Decision",
    "Effect",
    "PolicyContext",
    "PolicyEngine",
    "PolicyViolation",
    "Verdict",
    "current_context",
    "current_mode",
    "get_engine",
    "set_context",
    "set_engine",
]
