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

"""The four real entry points. Each submits a goal exactly the way a user or client would."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request

from artemis_voice.web.server import CSRF_HEADER
from harness import API_TOKEN, APPROVER, HERE, free_port, http, wait_for

ORIGINS = {
    "api": "task:frontend",
    "voice": "task:python_sdk",
    "mcp": "task:mcp",
    "cli": "task:cli",
    "cli_standalone": "task:cli",
}
ROOT = Path(__file__).resolve().parents[2]


def approver_headers() -> dict[str, str]:
    return {"X-Approver-Token": APPROVER}


def pending(base: str) -> list[dict]:
    status, body = http(
        "GET", f"{base}/api/policy/approvals", API_TOKEN, headers=approver_headers()
    )
    assert status == 200, body
    return body["pending"]


def decide(base: str, approval_id: str, verdict: str = "approve") -> tuple[int, dict]:
    return http(
        "POST", f"{base}/api/policy/approvals/{approval_id}/{verdict}", API_TOKEN,
        headers=approver_headers(),
    )  # fmt: skip


class Entries:
    def __init__(self, base: str, monkeypatch, env) -> None:
        self.base = base
        self.monkeypatch = monkeypatch
        self.env = env
        self.procs: list[subprocess.Popen] = []

    # ---- 1. HTTP /api/run (the real route, middleware and queue) -------------------------
    def api(self, goal: str) -> None:
        status, body = http("POST", f"{self.base}/api/run", API_TOKEN, {"goal": goal})
        assert status == 200, body

    # ---- 2. Voice web page server (real HTTP handler -> real ArtemisClient -> daemon) ------
    def voice(self, goal: str) -> None:
        from artemis_client import ArtemisClient
        from artemis_voice.config import VoiceConfig
        from artemis_voice.web.server import VoiceWebApp, serve

        if not hasattr(self, "_voice"):
            client = ArtemisClient(self.base, token=API_TOKEN)
            app = VoiceWebApp(client, object(), VoiceConfig(base_url=self.base, token=API_TOKEN))
            srv = serve(app, port=free_port())
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self._voice = (srv, f"http://127.0.0.1:{srv.server_address[1]}")
        _srv, url = self._voice
        csrf = {CSRF_HEADER: "1"}  # what the real web page sends
        status, body = http("POST", f"{url}/api/execute", None, {"goal": goal}, csrf)
        if status == 409 and body.get("error") == "confirmation_required":
            status, body = http(
                "POST", f"{url}/api/execute", None,
                {"goal": goal, "confirm_token": body["confirm_token"]}, csrf,
            )  # fmt: skip
        assert status == 200, body

    # ---- 3. MCP server tool (the real mobile_run_task) -----------------------------------
    def mcp(self, goal: str) -> None:
        from mcp_server.tools import task_runner

        self.monkeypatch.setattr(
            task_runner, "ensure_daemon_running", lambda **_k: (True, self.base)
        )
        run = getattr(task_runner.mobile_run_task, "fn", task_runner.mobile_run_task)
        result = run(task_desc=goal)
        assert result.get("status") != "rejected" and "error" not in result, result

    # ---- 4. The real CLI (`artemis run <goal>`), two real routes --------------------------
    def _spawn_cli(self, goal: str, session: str, extra: dict[str, str]) -> subprocess.Popen:
        env = {
            **os.environ,
            "ARTEMIS_SESSION_ID": session,
            "ARTEMIS_POLICY_DIR": str(self.env.policy_dir),
            "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
            "GOOGLE_API_KEY": "e2e-not-a-real-key",  # the model is never called
            "GEMINI_API_KEY": "e2e-not-a-real-key",
            **extra,
        }
        env.pop("ARTEMIS_TASK_INGRESS", None)
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "cli_entry.py"), "run", goal],
            env=env, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )  # fmt: skip
        self.procs.append(proc)
        return proc

    def cli(self, goal: str, session: str = "cli-daemon-session") -> subprocess.Popen:
        """`artemis run`: routed through the daemon queue (the CLI's default behaviour)."""
        port = self.base.rsplit(":", 1)[1]
        return self._spawn_cli(goal, session, {"ARTEMIS_DAEMON_PORT": port})

    def cli_standalone(self, goal: str, session: str = "cli-local-session") -> subprocess.Popen:
        """`artemis run --standalone` semantics: the agent loop runs inside the CLI process."""
        return self._spawn_cli(goal, session, {"ARTEMIS_STANDALONE": "1"})

    def submit(self, entry: str, goal: str) -> None:
        getattr(self, entry)(goal)

    def close(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
        if hasattr(self, "_voice"):
            self._voice[0].shutdown()
