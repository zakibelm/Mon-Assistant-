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

import pytest

from apps.admin_console.core.security import (
    BearerTokenMiddleware,
    require_token_for_remote_bind,
)


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _call(middleware, method, path, auth=None, scope_type="http"):
    sent = []
    headers = [(b"authorization", auth.encode())] if auth else []

    async def send(message):
        sent.append(message)

    scope = {"type": scope_type, "method": method, "path": path, "headers": headers}
    asyncio.run(middleware(scope, None, send))
    first = sent[0]
    return first.get("status", first.get("code"))


def test_disabled_without_token():
    mw = BearerTokenMiddleware(_ok, token="")
    assert _call(mw, "POST", "/api/run") == 200
    assert _call(mw, "GET", "/api/stream/device-live") == 200


def test_writes_and_sensitive_reads_need_the_token():
    mw = BearerTokenMiddleware(_ok, token="s3cret")
    for method, path in [
        ("POST", "/api/run"),
        ("GET", "/api/stream/device-live"),
        ("GET", "/api/sessions/1/video"),
        ("GET", "/images/shot.png"),
        ("GET", "/local_file"),
    ]:
        assert _call(mw, method, path) == 401, path
        assert _call(mw, method, path, "Bearer wrong") == 401
        assert _call(mw, method, path, "Bearer s3cret") == 200


def test_bare_token_and_other_schemes_are_refused():
    mw = BearerTokenMiddleware(_ok, token="s3cret")
    assert _call(mw, "POST", "/api/run", "s3cret") == 401
    assert _call(mw, "POST", "/api/run", "Basic s3cret") == 401
    assert _call(mw, "POST", "/api/run", "bearer s3cret") == 401


def test_websockets_are_closed_without_token():
    mw = BearerTokenMiddleware(_ok, token="s3cret")
    assert _call(mw, "GET", "/api/stream/ws", scope_type="websocket") == 1008
    assert _call(mw, "GET", "/api/stream/ws", "Bearer s3cret", scope_type="websocket") == 200


def test_status_is_private_and_only_health_is_open():
    mw = BearerTokenMiddleware(_ok, token="s3cret")
    assert _call(mw, "GET", "/api/status") == 401
    assert _call(mw, "GET", "/api/status", "Bearer s3cret") == 200
    assert _call(mw, "GET", "/api/health") == 200


def test_console_shell_stays_open():
    mw = BearerTokenMiddleware(_ok, token="s3cret")
    assert _call(mw, "GET", "/") == 200


def test_remote_bind_requires_a_token(monkeypatch):
    monkeypatch.delenv("ARTEMIS_API_TOKEN", raising=False)
    monkeypatch.delenv("ARTEMIS_ALLOW_INSECURE_REMOTE", raising=False)
    require_token_for_remote_bind("127.0.0.1")
    require_token_for_remote_bind("localhost")
    with pytest.raises(SystemExit):
        require_token_for_remote_bind("0.0.0.0")
    require_token_for_remote_bind("0.0.0.0", token="s3cret")
    monkeypatch.setenv("ARTEMIS_ALLOW_INSECURE_REMOTE", "1")
    require_token_for_remote_bind("192.168.1.5")


def test_production_ignores_the_insecure_remote_escape_hatch(monkeypatch):
    monkeypatch.delenv("ARTEMIS_API_TOKEN", raising=False)
    monkeypatch.setenv("ARTEMIS_ALLOW_INSECURE_REMOTE", "1")
    monkeypatch.setenv("ARTEMIS_ENV", "production")
    with pytest.raises(SystemExit):
        require_token_for_remote_bind("0.0.0.0")
    require_token_for_remote_bind("0.0.0.0", token="s3cret")


def test_production_profile_blocks_startup_without_token_even_without_bind_flag(monkeypatch):
    import asyncio as _asyncio

    from apps.admin_console import server

    monkeypatch.delenv("ARTEMIS_API_TOKEN", raising=False)
    monkeypatch.delenv("ARTEMIS_BIND_REMOTE", raising=False)
    monkeypatch.setenv("ARTEMIS_ENV", "production")

    async def enter():
        async with server._lifespan(server.app):
            pass

    with pytest.raises(SystemExit):
        _asyncio.run(enter())
