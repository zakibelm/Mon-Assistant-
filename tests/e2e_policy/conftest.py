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

"""E2E fixtures: the REAL admin daemon, with the worker brain swapped for a scripted stub.

Simulated, and only these: the phone (``fake_phone.FakePhone``), the model/worker brain
(``plan_runner``), and the device-readiness probe (there is no adb server in CI).
"""

import sys
import threading

from harness import (
    API_TOKEN,
    APPROVER,
    HERE,
    Env,
    free_port,
    http,
    wait_for,
)
import pytest
import uvicorn


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.set_screen("com.android.settings")
    return e


@pytest.fixture
def daemon(env, monkeypatch):
    """Start the real daemon app on a loopback port with the worker brain swapped for the stub."""
    monkeypatch.setenv("ARTEMIS_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("ARTEMIS_APPROVER_TOKENS", f"alice:{APPROVER}")
    monkeypatch.setenv("ARTEMIS_POLICY_DIR", str(env.policy_dir))
    monkeypatch.setenv("ARTEMIS_APPROVAL_TIMEOUT", "6")
    monkeypatch.setenv("FAKE_PHONE_LOG", str(env.phone_log))
    monkeypatch.setenv("FAKE_RESULT_LOG", str(env.result_log))
    monkeypatch.setenv("FAKE_SCREEN_FILE", str(env.screen))
    for var in ("ARTEMIS_ENV", "ARTEMIS_BIND_REMOTE", "ARTEMIS_POLICY_MODE"):
        monkeypatch.delenv(var, raising=False)

    from apps.admin_console import server
    from apps.admin_console.services.task_queue_service import TaskQueueService

    original = TaskQueueService._build_worker_invocation.__func__

    def stub_invocation(cls, task_item, run_key, sess_id, goal, profile, target):
        _cmd, worker_env = original(cls, task_item, run_key, sess_id, goal, profile, target)
        return [sys.executable, str(HERE / "fake_worker.py"), goal], worker_env

    monkeypatch.setattr(TaskQueueService, "_build_worker_invocation", classmethod(stub_invocation))
    from apps.admin_console.routers import tasks as tasks_router

    async def no_probe(*_a, **_k):
        return None

    monkeypatch.setattr(tasks_router.readiness_engine, "run_device_submission_probe", no_probe)

    port = free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    def alive() -> bool:
        try:
            return http("GET", f"{base}/api/health")[0] == 200
        except OSError:
            return False

    wait_for(alive, timeout=60)
    yield base
    srv.should_exit = True
    thread.join(timeout=15)


@pytest.fixture
def entries(daemon, env, monkeypatch):
    from entries import Entries

    e = Entries(daemon, monkeypatch, env)
    yield e
    e.close()
