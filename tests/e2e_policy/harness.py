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

"""E2E helpers for the REAL admin daemon (FastAPI app, middleware, /api/run, task queue, approver API).

Simulated, and only these: the phone (``fake_phone.FakePhone``), the model/worker brain
(``fake_worker.py``), and the device-readiness probe (there is no adb server in CI).
"""

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request


HERE = Path(__file__).resolve().parent
API_TOKEN = "e2e-api-token"
APPROVER = "e2e-approver-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Env:
    """Paths the test and the worker share."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.policy_dir = root / "policy"
        self.policy_dir.mkdir()
        self.phone_log = root / "phone.jsonl"
        self.result_log = root / "results.jsonl"
        self.screen = root / "screen.json"

    def set_screen(self, package: str, elements: list[dict] | None = None) -> None:
        self.screen.write_text(json.dumps({"package": package, "elements": elements or []}))

    def effects(self) -> list[dict]:
        return _jsonl(self.phone_log)

    def results(self) -> list[dict]:
        return _jsonl(self.result_log)

    def audit(self) -> list[dict]:
        return _jsonl(self.policy_dir / "audit.jsonl")


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def element(text: str, bounds=(0, 0, 200, 200)) -> dict:
    return {
        "text": text,
        "clickable": "true",
        "parsed_bounds": dict(zip(("left", "top", "right", "bottom"), bounds, strict=True)),
    }


def http(method: str, url: str, token: str | None = None, body: dict | None = None, headers=None):
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body else None
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw or b"{}")
        except ValueError:
            return err.code, {"raw": raw.decode(errors="replace")}
