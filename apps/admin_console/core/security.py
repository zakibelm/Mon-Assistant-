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

"""Same-origin security boundary for the Artemis console.

Artemis runs without user accounts: whoever can reach the TCP port is the
operator. For that model to hold for a server that browsers also talk to,
two browser-borne attack vectors must be closed even on a loopback bind:

- DNS rebinding: a page at ``attacker.example`` re-points its DNS record at
  127.0.0.1 so the victim's browser reads API responses under the attacker's
  origin. Blocked by validating the ``Host`` header.
- Cross-site request forgery: any web page can fire side-effectful requests
  at ``http://127.0.0.1:8000`` from a visitor's browser. Blocked by requiring
  the ``Origin`` header, when a browser sends one, to match the request host.

Non-browser clients (the SDK, curl, the CLI, MCP) send no ``Origin`` header
and pass through untouched. Remote access is expected to arrive over a
network-level tunnel (Tailscale, SSH port forward); a tunnel hostname that is
not an IP literal can be admitted via ``ARTEMIS_ALLOWED_HOSTS``.
"""

from __future__ import annotations

import ipaddress
import json
import os
from urllib.parse import urlsplit

_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
)

# Paths whose responses may hold task data, media, or diagnostics and must not
# land in shared caches or survive on disk after the session.
_NO_STORE_PREFIXES = ("/api/",)


def _hostname(netloc: str) -> str:
    """Extract the lowercase hostname from a ``host[:port]`` netloc."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):  # IPv6 literal, e.g. [::1]:8000
        return netloc.partition("]")[0].lstrip("[")
    return netloc.rpartition(":")[0] if netloc.count(":") == 1 else netloc


def _is_ip_literal(host: str) -> bool:
    """An IP-literal Host cannot be forged through DNS rebinding: a browser
    only sends ``Host: 192.168.1.5`` when it actually connected to that IP."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def allowed_hostnames() -> frozenset[str]:
    """Hostnames (not IP literals) accepted in Host and Origin headers."""
    names = {"localhost"}
    extra = os.environ.get("ARTEMIS_ALLOWED_HOSTS", "")
    names.update(part.strip().lower() for part in extra.split(",") if part.strip())
    return frozenset(names)


def host_is_allowed(host_header: str) -> bool:
    host = _hostname(host_header)
    if not host:
        return False
    return _is_ip_literal(host) or host in allowed_hostnames()


def origin_is_allowed(origin_header: str, host_header: str) -> bool:
    """A browser request is acceptable when its page is same-origin with this
    server, or was served from an explicitly allowed tunnel hostname."""
    origin = origin_header.strip().lower()
    if not origin or origin == "null":
        return False
    origin_netloc = urlsplit(origin).netloc
    if not origin_netloc:
        return False
    if origin_netloc == host_header.strip().lower():
        return True
    return _hostname(origin_netloc) in allowed_hostnames()


class SameOriginBoundaryMiddleware:
    """Pure ASGI middleware enforcing the Host/Origin boundary and appending
    baseline security headers. Pure ASGI (not BaseHTTPMiddleware) so future
    WebSocket endpoints cannot slip past the Origin check."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if os.environ.get("ARTEMIS_DISABLE_ORIGIN_GUARD", "").lower() in {"1", "true", "yes"}:
            await self._forward(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host_header = headers.get("host", "")
        origin_header = headers.get("origin")

        if not host_is_allowed(host_header):
            await self._reject(
                scope,
                send,
                "Unrecognized Host header. Access Artemis via localhost or an IP address, "
                "or add your tunnel hostname to ARTEMIS_ALLOWED_HOSTS.",
            )
            return

        if origin_header is not None and not origin_is_allowed(origin_header, host_header):
            await self._reject(
                scope,
                send,
                "Cross-origin browser requests are not accepted by the Artemis console.",
            )
            return

        await self._forward(scope, receive, send)

    async def _forward(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                existing = {name.lower() for name, _ in raw}
                for name, value in _SECURITY_HEADERS:
                    if name not in existing:
                        raw.append((name, value))
                if b"cache-control" not in existing and path.startswith(_NO_STORE_PREFIXES):
                    raw.append((b"cache-control", b"no-store"))
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    async def _reject(scope, send, detail: str):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    *_SECURITY_HEADERS,
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class BearerTokenMiddleware:
    """Optional shared-secret check on every sensitive route (reads included).

    Off unless ``ARTEMIS_API_TOKEN`` is set, so loopback setups are unchanged. When set,
    any request (HTTP or WebSocket, any method) to ``/api/``, ``/images/``, ``/videos/``
    or ``/local_file`` needs ``Authorization: Bearer <token>``: task control, session
    data, media and the live phone-screen stream (``/api/stream/device-live``).
    Only ``/api/health`` (a bare ready flag) stays open; ``/api/status`` exposes goals, sessions and device ids, so it is protected. The scheme must
    be exactly ``Bearer``; a bare token is refused.
    """

    _PROTECTED_PREFIXES = ("/api/", "/images/", "/videos/", "/local_file")
    _OPEN_PATHS = frozenset({"/api/health"})

    def __init__(self, app, token: str | None = None) -> None:
        self.app = app
        self.token = token if token is not None else os.environ.get("ARTEMIS_API_TOKEN", "")

    def _protected(self, path: str) -> bool:
        return path.startswith(self._PROTECTED_PREFIXES) and path not in self._OPEN_PATHS

    @staticmethod
    def _bearer(headers: list[tuple[bytes, bytes]]) -> str | None:
        for name, value in headers:
            if name == b"authorization":
                scheme, _, credentials = value.decode("latin-1").partition(" ")
                return credentials.strip() if scheme == "Bearer" else None
        return None

    async def __call__(self, scope, receive, send) -> None:
        if self.token and scope["type"] in {"http", "websocket"} and self._protected(scope["path"]):
            import hmac

            supplied = self._bearer(scope["headers"])
            if supplied is None or not hmac.compare_digest(supplied.encode(), self.token.encode()):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = json.dumps({"detail": "invalid_or_missing_token"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def require_token_for_remote_bind(host: str, token: str | None = None) -> None:
    """Refuse a non-loopback bind unless a token is set (or the operator opts out)."""
    token = token if token is not None else os.environ.get("ARTEMIS_API_TOKEN", "")
    production = os.environ.get("ARTEMIS_ENV", "").lower() == "production"
    if token or (os.environ.get("ARTEMIS_ALLOW_INSECURE_REMOTE") == "1" and not production):
        return
    try:
        loopback = ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback:
        raise SystemExit(
            f"Refusing to listen on {host}: set ARTEMIS_API_TOKEN (ARTEMIS_ALLOW_INSECURE_REMOTE=1 "
            "is a development-only opt-out, ignored when ARTEMIS_ENV=production). "
            "Prefer a Tailscale/SSH tunnel on loopback."
        )
