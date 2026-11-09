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

"""Execution-path coverage: static guarantees that no code path reaches the phone unguarded.

These are structural tests over the source tree. They are NOT end-to-end tests on a real Android
device; they fail if someone adds a new way of reaching ``adb`` that skips the policy.
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from artemis.drivers.base import BaseDeviceDriver
from artemis.drivers.factory import create_driver, get_driver
from artemis.policy import PolicyViolation
from artemis.policy.guard import PolicyGuardedDriver
from artemis.tools import command_tool

ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIRS = ["artemis", "apps", "packages", "mcp_server", "third_party"]


def _py_files():
    for name in SOURCE_DIRS:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            parts = set(path.relative_to(ROOT).parts)
            if parts & {"tests", "test", ".venv", "node_modules", "__pycache__"}:
                continue
            yield path


def test_android_driver_is_constructed_only_in_the_factory():
    offenders = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in {"artemis/drivers/factory.py", "artemis/drivers/android/adb_driver.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name == "AndroidAdbDriver":
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"AndroidAdbDriver built outside the factory: {offenders}"


def test_active_driver_is_assigned_only_by_known_sites():
    allowed = {"artemis/drivers/factory.py", "artemis/mcp/actuators/mock.py"}
    offenders = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel in allowed or "_active_driver" not in text:
            continue
        tree = ast.parse(text, filename=rel)
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "_active_driver":
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"driver cached without going through the factory: {offenders}"


def _functions_spawning_adb(tree):
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        spawns = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "create_subprocess_exec"
        ]
        if spawns:
            yield fn, spawns


def test_every_adb_spawn_in_command_tool_is_preceded_by_the_policy_gate():
    path = ROOT / "artemis/tools/command_tool.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = 0
    for fn, spawns in _functions_spawning_adb(tree):
        gates = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_policy_gate"
        ]
        assert gates, f"{fn.name} spawns a process without _policy_gate"
        for spawn in spawns:
            found += 1
            assert min(gates) < spawn.lineno, f"{fn.name}: gate must come before the spawn"
    assert found >= 2  # RunAdbCommandTool + RunShortAdbCommandTool


def test_guard_covers_every_effectful_driver_operation():
    """A new abstract operation on the driver must be consciously classified, not silently passed."""
    read_only = {"device_id", "screen_size", "connect", "disconnect", "get_screen_data",
                 "get_current_package"}  # fmt: skip
    abstract = {
        name
        for name, member in vars(BaseDeviceDriver).items()
        if getattr(member, "__isabstractmethod__", False)
        or getattr(getattr(member, "fget", None), "__isabstractmethod__", False)
    }
    gated = set()
    for name in abstract - read_only:
        method = vars(PolicyGuardedDriver).get(name)
        assert method is not None, f"guard does not override {name}"
        assert "_gate" in method.__code__.co_names or "check_shell" in method.__code__.co_names \
            or name in {"start_video_recording", "stop_video_recording"}, name  # fmt: skip
        gated.add(name)
    assert {"tap", "input_text", "execute_shell", "launch_app", "press_key"} <= gated


def _mock_ctx():
    device = SimpleNamespace(
        device_id="mock-1", device_width=1080, device_height=2400, mobile_platform="mock"
    )
    return SimpleNamespace(device=device, adb_client=None, ui_adb_client=None, _active_driver=None)


def test_create_driver_returns_the_guard():
    driver = create_driver(_mock_ctx())
    assert isinstance(driver, PolicyGuardedDriver)
    with pytest.raises(AttributeError):
        _ = driver.device


def test_get_driver_caches_the_guard_not_the_raw_driver():
    ctx = _mock_ctx()
    assert isinstance(get_driver(ctx), PolicyGuardedDriver)
    assert ctx._active_driver is get_driver(ctx)


def test_mock_driver_is_guarded_end_to_end(engine):
    driver = create_driver(_mock_ctx())
    with pytest.raises(PolicyViolation):
        asyncio.run(driver.execute_shell("rm -rf /sdcard"))


# ---- the tools that spawn adb on their own --------------------------------------------------


class _Denying:
    """A driver whose policy refuses everything; proves the tool never reaches the subprocess."""

    device_id = "dev"

    async def check_shell(self, command):
        from artemis.policy.model import Decision, Verdict

        raise PolicyViolation(Verdict(Decision.DENY, "v1/test", "no"), "t")


def test_policy_gate_blocks_and_reports(engine):
    msg = asyncio.run(command_tool._policy_gate("rm -rf /sdcard", _Denying(), None))
    assert msg and "policy_deny" in msg


def test_policy_gate_without_a_driver_still_enforces(engine):
    ctx = SimpleNamespace(device=SimpleNamespace(device_id="dev"), _active_driver=None)
    assert asyncio.run(command_tool._policy_gate("rm -rf /sdcard", None, ctx))
    assert asyncio.run(command_tool._policy_gate("getprop ro.x", None, ctx)) is None


@pytest.mark.parametrize("tool_name", ["RunAdbCommandTool", "RunShortAdbCommandTool"])
def test_adb_tools_never_spawn_when_denied(engine, monkeypatch, tool_name):
    spawned = []

    async def fake_spawn(*a, **k):
        spawned.append(a)
        raise AssertionError("adb must not be spawned for a denied command")

    monkeypatch.setattr(command_tool.asyncio, "create_subprocess_exec", fake_spawn)
    tool = getattr(command_tool, tool_name)()
    ctx = SimpleNamespace(
        device=SimpleNamespace(device_id="dev"), _active_driver=_Denying(), adb_client=None
    )
    result = asyncio.run(tool.execute(ctx=ctx, command="rm -rf /sdcard"))
    assert spawned == []
    assert "policy_deny" in str(getattr(result, "error", result))


def test_shell_variable_expansion_is_refused_by_the_tool(engine):
    """Documented behaviour change: `echo $VAR` cannot be analyzed statically, so it is blocked."""
    from artemis.policy.guard import PolicyGuardedDriver
    from policy_helpers import RecordingDriver

    guard = PolicyGuardedDriver(RecordingDriver(), engine)
    msg = asyncio.run(command_tool._policy_gate("echo $ARTEMIS_TEST_ENV", guard, None))
    assert msg and "policy_unknown" in msg
    assert asyncio.run(command_tool._policy_gate("printenv ARTEMIS_TEST_ENV", guard, None)) is None
