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

"""BearerTokenMiddleware protects writes only when a token is configured."""

import asyncio

from apps.admin_console.core.security import BearerTokenMiddleware


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _call(middleware, method, path, auth=None):
    sent = []
    headers = [(b"authorization", auth.encode())] if auth else []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    asyncio.run(middleware(scope, None, send))
    return sent[0]["status"]


def test_disabled_without_token():
    mw = BearerTokenMiddleware(_ok, token="")
    assert _call(mw, "POST", "/api/run") == 200


def test_write_requires_token_when_set():
    mw = BearerTokenMiddleware(_ok, token="s3cret")
    assert _call(mw, "POST", "/api/run") == 401
    assert _call(mw, "POST", "/api/run", "Bearer wrong") == 401
    assert _call(mw, "POST", "/api/run", "Bearer s3cret") == 200
    assert _call(mw, "DELETE", "/api/sessions/1", "Bearer s3cret") == 200


def test_reads_and_non_api_paths_stay_open():
    mw = BearerTokenMiddleware(_ok, token="s3cret")
    assert _call(mw, "GET", "/api/status") == 200
    assert _call(mw, "POST", "/other") == 200
