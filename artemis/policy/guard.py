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

"""PolicyGuardedDriver: the single choke point between every caller and the phone.

``create_driver`` wraps whatever driver it builds, so the voice layer, ``/api/run``, MCP,
the CLI and the console all reach Android only through :meth:`_gate`.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import re
from typing import Any

from artemis.drivers.base import BaseDeviceDriver, KeyCode, ScreenData
from artemis.policy.engine import PolicyEngine, get_engine
from artemis.policy.model import Action, current_context
from artemis.policy.rules import parse_shell

# Raw handles that would let a caller run shell/input behind the guard's back.
_RAW_HANDLES = frozenset(
    {"device", "_device", "adb_client", "ui_adb_client", "ui_client", "_ui_client", "adb", "_adb"}
)
_LOOKUP_TIMEOUT = 3.0


def _quad(values: list[Any]) -> tuple[int, int, int, int]:
    left, top, right, bottom = (int(v) for v in values)
    return (left, top, right, bottom)


def _bounds(elem: dict[str, Any]) -> tuple[int, int, int, int] | None:
    parsed = elem.get("parsed_bounds")
    if isinstance(parsed, dict):
        keys = ("left", "top", "right", "bottom")
        if all(isinstance(parsed.get(k), (int, float)) for k in keys):
            return _quad([parsed[k] for k in keys])
    raw = elem.get("bounds")
    if (
        isinstance(raw, (list, tuple))
        and len(raw) == 4
        and all(isinstance(v, (int, float)) for v in raw)
    ):
        return _quad(list(raw))
    if isinstance(raw, str):
        m = re.fullmatch(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]", raw)
        if m:
            return _quad(list(m.groups()))
    return None


def _label(elem: dict[str, Any]) -> str:
    return str(
        elem.get("text") or elem.get("content-desc") or elem.get("content_desc") or ""
    ).strip()


def label_at(elements: list[dict[str, Any]], x: int, y: int) -> str:
    """What the user would read at (x, y): the smallest clickable box and the text inside it."""
    boxes = []
    for e in elements:
        b = _bounds(e)
        if b and b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            boxes.append((e, b))
    if not boxes:
        return ""
    area = lambda b: max(1, (b[2] - b[0]) * (b[3] - b[1]))  # noqa: E731
    clickable = [(e, b) for e, b in boxes if str(e.get("clickable")).lower() == "true"]
    anchor = min(clickable or boxes, key=lambda eb: area(eb[1]))
    parts = [_label(anchor[0])]
    ab = anchor[1]
    for e in elements:
        b = _bounds(e)
        if b and ab[0] <= b[0] and ab[1] <= b[1] and b[2] <= ab[2] and b[3] <= ab[3]:
            parts.append(_label(e))
    return " ".join(p for p in parts if p)[:120]


class PolicyGuardedDriver(BaseDeviceDriver):
    def __init__(self, inner: BaseDeviceDriver, engine: PolicyEngine | None = None) -> None:
        self._inner = inner
        self._engine = engine

    @property
    def engine(self) -> PolicyEngine:
        return self._engine or get_engine()

    def __getattr__(self, name: str) -> Any:
        if name in _RAW_HANDLES:
            raise AttributeError(
                f"{name!r} is not exposed through the policy guard; use the driver methods"
            )
        return getattr(self._inner, name)

    # ---- context gathering (best effort, never raises) -------------------------------
    async def _foreground(self) -> str | None:
        try:
            return await asyncio.wait_for(self._inner.get_current_package(), _LOOKUP_TIMEOUT)
        except Exception:  # noqa: BLE001
            return None

    async def _elements(self) -> list[dict[str, Any]]:
        try:
            data = await asyncio.wait_for(
                self._inner.get_screen_data(skip_settling=True), _LOOKUP_TIMEOUT
            )
            return list(data.ui_elements or [])
        except Exception:  # noqa: BLE001
            return []

    async def _gate(self, op: str, params: dict[str, Any], *, point: tuple[int, int] | None = None,
                    labels_for: list[tuple[int, int]] | None = None) -> str:  # fmt: skip
        package = await self._foreground()
        label = None
        labels: dict[tuple[int, int], str] = {}
        wanted = ([point] if point else []) + (labels_for or [])
        if wanted:
            elements = await self._elements()
            for xy in wanted:
                labels[xy] = label_at(elements, *xy)
            label = labels.get(point) if point else None
        action = Action(
            op=op,
            device_id=self._inner.device_id,
            params=params,
            foreground_package=package,
            target_label=label or None,
            context=current_context(),
            labels={k: v for k, v in labels.items() if v},
        )
        return await self.engine.enforce(action)

    async def _run(self, trace: str, call):  # noqa: ANN001
        try:
            result = await call
        except Exception as exc:
            self.engine.record_outcome(trace, False, type(exc).__name__)
            raise
        self.engine.record_outcome(trace, bool(result) if isinstance(result, bool) else True)
        return result

    # ---- BaseDeviceDriver -----------------------------------------------------------
    @property
    def device_id(self) -> str:
        return self._inner.device_id

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._inner.screen_size

    async def connect(self) -> None:
        await self._inner.connect()

    async def disconnect(self) -> None:
        await self._inner.disconnect()

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        return await self._inner.get_screen_data(skip_settling=skip_settling)

    async def get_current_package(self) -> str | None:
        return await self._inner.get_current_package()

    async def tap(
        self, x: int, y: int, duration_ms: int = 100, times: int = 1, delay_ms: int = 100
    ) -> bool:
        trace = await self._gate(
            "tap", {"x": int(x), "y": int(y), "times": int(times)}, point=(int(x), int(y))
        )
        return await self._run(
            trace, self._inner.tap(x, y, duration_ms=duration_ms, times=times, delay_ms=delay_ms)
        )

    async def long_press(self, x: int, y: int, duration_ms: int = 1000) -> bool:
        trace = await self._gate("long_press", {"x": int(x), "y": int(y)}, point=(int(x), int(y)))
        return await self._run(trace, self._inner.long_press(x, y, duration_ms=duration_ms))

    async def swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int = 800
    ) -> bool:
        trace = await self._gate(
            "swipe",
            {"x": int(start_x), "y": int(start_y), "end_x": int(end_x), "end_y": int(end_y)},
        )
        return await self._run(
            trace, self._inner.swipe(start_x, start_y, end_x, end_y, duration_ms=duration_ms)
        )

    async def swipe_direction(self, direction: Any, duration_ms: int = 800) -> bool:
        trace = await self._gate(
            "swipe", {"direction": str(getattr(direction, "value", direction))}
        )
        return await self._run(
            trace, self._inner.swipe_direction(direction, duration_ms=duration_ms)
        )

    async def input_text(self, text: str, clear_existing: bool = True) -> bool:
        params = {"length": len(text), "clear": bool(clear_existing),
                  "sha256": hashlib.sha256(text.encode()).hexdigest()}  # fmt: skip
        trace = await self._gate("input_text", params)
        return await self._run(trace, self._inner.input_text(text, clear_existing=clear_existing))

    async def press_key(self, key: KeyCode | str | int) -> bool:
        trace = await self._gate("press_key", {"key": str(getattr(key, "value", key))})
        return await self._run(trace, self._inner.press_key(key))

    async def launch_app(self, package_name: str) -> bool:
        trace = await self._gate("launch_app", {"package": package_name})
        return await self._run(trace, self._inner.launch_app(package_name))

    async def stop_app(self, package_name: str) -> bool:
        trace = await self._gate("stop_app", {"package": package_name})
        return await self._run(trace, self._inner.stop_app(package_name))

    async def check_shell(self, command: str) -> str:
        """Authorize a shell line WITHOUT running it (for tools that spawn adb themselves)."""
        taps = []
        for seg in parse_shell(command).segments:
            if len(seg) >= 4 and seg[0] == "input" and seg[1] == "tap":
                try:
                    taps.append((int(seg[2]), int(seg[3])))
                except ValueError:
                    pass
        return await self._gate("shell", {"command": command}, labels_for=taps)

    async def execute_shell(self, command: str, timeout_seconds: float = 15.0) -> str:
        trace = await self.check_shell(command)
        return await self._run(
            trace, self._inner.execute_shell(command, timeout_seconds=timeout_seconds)
        )

    async def start_video_recording(self, output_dir: Path | None = None) -> None:
        await self._inner.start_video_recording(output_dir)

    async def stop_video_recording(self) -> str | None:
        return await self._inner.stop_video_recording()
