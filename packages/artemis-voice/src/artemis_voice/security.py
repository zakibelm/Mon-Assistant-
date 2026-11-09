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

"""Refuse to send voice-driven device commands over an unsafe channel.

The Artemis daemon admin API has no built-in authentication (see the
``artemis-client`` README). Anything that can reach it can drive the phone, so
the voice layer only talks to it over loopback (SSH tunnel), a Tailscale
address, or HTTPS with a bearer token.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from artemis_voice.errors import InsecureEndpointError

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def is_loopback(host: str) -> bool:
    if host in {"localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_tailscale(host: str) -> bool:
    if host.endswith(".ts.net"):
        return True
    try:
        return ipaddress.ip_address(host) in _TAILSCALE_NET
    except ValueError:
        return False


def validate_endpoint(base_url: str, token: str | None, *, allow_insecure: bool = False) -> None:
    """Raise :class:`InsecureEndpointError` unless the endpoint is acceptable."""
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InsecureEndpointError(f"Invalid Artemis endpoint: {base_url!r}")
    host = parsed.hostname
    if is_loopback(host) or is_tailscale(host):
        return
    if parsed.scheme == "https" and token:
        return
    if allow_insecure:
        return
    raise InsecureEndpointError(
        f"Refusing to send voice commands to {base_url!r}: the Artemis daemon has no "
        "built-in authentication. Use an SSH tunnel (ssh -L 8000:127.0.0.1:8000 host), "
        "a Tailscale address, or an HTTPS reverse proxy with a bearer token "
        "(--token). Pass --allow-insecure only on a network you fully control."
    )
