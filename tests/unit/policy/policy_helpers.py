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

"""Test helpers: a recording driver standing in for the phone, and a simulated out-of-band approver."""

import asyncio
from pathlib import Path
import threading
import time
from typing import Any

from artemis.drivers.base import BaseDeviceDriver, ScreenData
from artemis.policy.approvals import ApprovalStore


class RecordingDriver(BaseDeviceDriver):
    """Stands in for the phone: records every effect that actually reaches it."""

    def __init__(self, package: str = "com.android.settings", elements=None) -> None:
        self.effects: list[tuple[str, Any]] = []
        self.package = package
        self.elements = elements or []
        self.device = object()  # a raw handle the guard must hide

    @property
    def device_id(self) -> str:
        return "test-device"

    @property
    def screen_size(self) -> tuple[int, int]:
        return (1080, 2400)

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        return ScreenData(screenshot_bytes=b"", screenshot_base64="", ui_elements=self.elements)

    async def tap(self, x, y, duration_ms=100, times=1, delay_ms=100) -> bool:
        self.effects.append(("tap", (x, y)))
        return True

    async def long_press(self, x, y, duration_ms=1000) -> bool:
        self.effects.append(("long_press", (x, y)))
        return True

    async def swipe(self, start_x, start_y, end_x, end_y, duration_ms=800) -> bool:
        self.effects.append(("swipe", (start_x, start_y)))
        return True

    async def swipe_direction(self, direction, duration_ms=800) -> bool:
        self.effects.append(("swipe_direction", direction))
        return True

    async def input_text(self, text, clear_existing=True) -> bool:
        self.effects.append(("input_text", text))
        return True

    async def press_key(self, key) -> bool:
        self.effects.append(("press_key", key))
        return True

    async def launch_app(self, package_name) -> bool:
        self.effects.append(("launch_app", package_name))
        return True

    async def stop_app(self, package_name) -> bool:
        self.effects.append(("stop_app", package_name))
        return True

    async def get_current_package(self) -> str | None:
        return self.package

    async def execute_shell(self, command, timeout_seconds=15.0) -> str:
        self.effects.append(("shell", command))
        return "ok"

    async def start_video_recording(self, output_dir: Path | None = None) -> None: ...

    async def stop_video_recording(self) -> str | None:
        return None


def element(text: str, bounds=(0, 0, 100, 100), clickable=True) -> dict:
    return {
        "text": text,
        "clickable": "true" if clickable else "false",
        "parsed_bounds": dict(zip(("left", "top", "right", "bottom"), bounds, strict=True)),
    }


def run(coro):
    return asyncio.run(coro)


def approve_later(store: ApprovalStore, approve: bool = True, delay: float = 0.15, who="alice"):
    """Simulate the human on the out-of-band channel."""
    result: dict[str, Any] = {}

    def worker():
        deadline = time.time() + 3
        while time.time() < deadline:
            pending = store.list_pending()
            if pending:
                result["id"] = pending[0]["id"]
                time.sleep(delay)
                result["ok"] = store.decide(pending[0]["id"], approve, who)
                return
            time.sleep(0.01)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, result
