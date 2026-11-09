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

"""A fake phone for the policy E2E campaign. Only the phone (and the model) are simulated."""

import json
import os
from pathlib import Path
from typing import Any

from artemis.drivers.base import BaseDeviceDriver, ScreenData


def _read_screen() -> dict[str, Any]:
    path = os.environ.get("FAKE_SCREEN_FILE")
    if path and Path(path).exists():
        return json.loads(Path(path).read_text())
    return {"package": "com.android.settings", "elements": []}


def log_effect(kind: str, detail: Any) -> None:
    path = os.environ.get("FAKE_PHONE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "effect": kind, "detail": detail}) + "\n")


class FakePhone(BaseDeviceDriver):
    """Records every effect that reaches the 'device'. The screen is read from a file."""

    def __init__(self) -> None:
        self.device = object()

    @property
    def device_id(self) -> str:
        return "fake-phone"

    @property
    def screen_size(self) -> tuple[int, int]:
        return (1080, 2400)

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        return ScreenData(
            screenshot_bytes=b"", screenshot_base64="", ui_elements=_read_screen()["elements"]
        )

    async def get_current_package(self) -> str | None:
        return _read_screen()["package"]

    async def tap(self, x, y, duration_ms=100, times=1, delay_ms=100) -> bool:
        log_effect("tap", [x, y])
        return True

    async def long_press(self, x, y, duration_ms=1000) -> bool:
        log_effect("long_press", [x, y])
        return True

    async def swipe(self, start_x, start_y, end_x, end_y, duration_ms=800) -> bool:
        log_effect("swipe", [start_x, start_y, end_x, end_y])
        return True

    async def swipe_direction(self, direction, duration_ms=800) -> bool:
        log_effect("swipe_direction", str(direction))
        return True

    async def input_text(self, text, clear_existing=True) -> bool:
        log_effect("input_text", len(text))
        return True

    async def press_key(self, key) -> bool:
        log_effect("press_key", str(key))
        return True

    async def launch_app(self, package_name) -> bool:
        log_effect("launch_app", package_name)
        return True

    async def stop_app(self, package_name) -> bool:
        log_effect("stop_app", package_name)
        return True

    async def execute_shell(self, command, timeout_seconds=15.0) -> str:
        log_effect("shell", command)
        return "ok"

    async def start_video_recording(self, output_dir=None) -> None: ...

    async def stop_video_recording(self) -> str | None:
        return None
