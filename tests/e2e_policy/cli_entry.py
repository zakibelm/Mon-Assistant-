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

"""The REAL ``artemis`` CLI, with only the model/agent loop (``run_automation``) replaced."""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import artemis.interfaces.cli.commands.run as run_module  # noqa: E402
from plan_runner import run_plan  # noqa: E402


async def scripted_run_automation(config, goal, **_kwargs):
    """The agent loop (model + planner) is replaced; CLI parsing, routing and env setup stay real."""
    await run_plan(goal)


run_module.run_automation = scripted_run_automation
run_module.display_local_device_status = lambda *_a, **_k: None  # no adb in CI

from artemis.interfaces.cli.main import cli  # noqa: E402

cli()
