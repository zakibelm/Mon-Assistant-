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

"""Scripted plans standing in for the model. They go through the REAL driver factory."""

import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import artemis.drivers.factory as factory  # noqa: E402
from artemis.policy import PolicyViolation, current_context  # noqa: E402
from fake_phone import FakePhone  # noqa: E402

factory._build_driver = lambda ctx: FakePhone()  # only the phone is replaced

PLANS = {
    "open_settings": [("launch_app", "com.android.settings")],
    "send_message": [("press_key", "back"), ("tap", 100, 100)],
    "wipe_sdcard": [("shell", "rm -rf /sdcard")],
    "pay": [("tap", 300, 100)],
    "var_expansion": [("shell", "echo $HOME")],
    "swipe_away": [("swipe", 900, 800, 100, 800)],
    "benign_then_sensitive": [
        ("swipe", 500, 1500, 500, 500),
        ("tap", 100, 100),
        ("press_key", "back"),
    ],
}


async def run_plan(goal: str) -> None:
    plan = PLANS[goal.removeprefix("scenario:")]
    ctx = SimpleNamespace(
        device=SimpleNamespace(
            device_id="fake-phone", device_width=1080, device_height=2400, mobile_platform="mock"
        ),
        adb_client=None,
        ui_adb_client=None,
        _active_driver=None,
    )
    driver = factory.get_driver(ctx)
    context = current_context()
    results = [{"context": [context.origin, context.session_id, context.requester]}]
    for step in plan:
        op, args = step[0], step[1:]
        try:
            await getattr(driver, {"shell": "execute_shell"}.get(op, op))(*args)
            results.append({"step": list(step), "ok": True})
        except PolicyViolation as violation:
            results.append({"step": list(step), "ok": False, "policy": violation.verdict.policy_id})
            break
    out = os.environ.get("FAKE_RESULT_LOG")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "goal": goal, "results": results}) + "\n")
