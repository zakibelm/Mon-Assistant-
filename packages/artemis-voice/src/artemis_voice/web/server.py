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

"""Push-to-talk web page, for tablets/phones that have a browser but no audio stack.

The browser records the microphone and uploads 16 kHz mono WAV. Whisper runs
on the server. The page always shows what was heard and requires a tap to
execute, so a misheard command never reaches the phone silently.

Hardening: loopback bind by default, optional bearer token, Host allow-list
(DNS rebinding), mandatory custom header + Origin check on writes (CSRF), and
server-side sensitivity re-check (the client cannot skip the confirmation).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlparse

from artemis_client.errors import ArtemisClientError

from artemis_voice.audio.wav import MAX_WAV_BYTES, wav_bytes_to_pcm
from artemis_voice.config import VoiceConfig
from artemis_voice.errors import (
    AudioError,
    InsecureEndpointError,
    SpeechRecognitionError,
    TTSError,
)
from artemis_voice.intent import IntentKind, parse_intent
from artemis_voice.security import is_loopback
from artemis_voice.stt.base import SpeechToText
from artemis_voice.stt.filters import is_hallucination
from artemis_voice.tts.base import Synthesizer, clean_for_speech

CSRF_HEADER = "X-Artemis-Voice"
MAX_JSON_BYTES = 64 * 1024
MAX_SPEAK_CHARS = 400
CONFIRM_TTL_SECONDS = 120
PIN_MAX_FAILURES = 5
PIN_LOCK_SECONDS = 60


class VoiceWebApp:
    """Framework-free request logic (the HTTP handler is a thin shell)."""

    def __init__(
        self,
        client: Any,
        stt: SpeechToText,
        config: VoiceConfig,
        *,
        token: str | None = None,
        approval_pin: str | None = None,
        production: bool | None = None,
        allowed_hosts: tuple[str, ...] = (),
        synth: Synthesizer | None = None,
    ) -> None:
        self.client = client
        self.stt = stt
        self.synth = synth
        self.config = config
        self.token = token
        # A separate secret that only the human knows (never given to automation):
        # sensitive actions need it IN ADDITION to the API token and the confirm token.
        self.approval_pin = approval_pin or None
        self.production = (
            os.environ.get("ARTEMIS_ENV", "").lower() == "production"
            if production is None
            else production
        )
        self._lock = threading.Lock()
        self._pin_failures = 0
        self._pin_locked_until = 0.0
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.current_task: str | None = None
        self._confirm_key = secrets.token_bytes(32)
        self._spent: dict[str, float] = {}

    # -- access control -----------------------------------------------------

    def host_allowed(self, host_header: str | None) -> bool:
        if not host_header:
            return False
        try:
            host = (urlparse("//" + host_header).hostname or "").lower()
        except ValueError:
            return False
        return bool(host) and (is_loopback(host) or host in self.allowed_hosts)

    def token_ok(self, authorization: str | None) -> bool:
        if not self.token:
            return True
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme != "Bearer":
            return False
        supplied = supplied.strip()
        return hmac.compare_digest(supplied.encode(), self.token.encode())

    # -- API ------------------------------------------------------------------

    def transcribe(self, wav_bytes: bytes) -> tuple[int, dict[str, Any]]:
        try:
            pcm, rate = wav_bytes_to_pcm(wav_bytes)
            text = self.stt.transcribe(pcm, rate).text.strip()
        except AudioError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
        except SpeechRecognitionError as exc:
            return HTTPStatus.BAD_GATEWAY, {"error": str(exc)}
        if is_hallucination(text):
            return HTTPStatus.OK, {"text": "", "kind": IntentKind.EMPTY.value}
        return HTTPStatus.OK, self._describe(text)

    def _describe(self, text: str) -> dict[str, Any]:
        cfg = self.config
        intent = parse_intent(
            text,
            wake_words=cfg.wake_words,
            require_wake_word=cfg.require_wake_word,
            extra_sensitive_words=cfg.extra_sensitive_words,
        )
        return {
            "text": text,
            "kind": intent.kind.value,
            "goal": intent.goal,
            "sensitive": intent.sensitive,
            "needs_confirmation": intent.sensitive and cfg.confirm_sensitive,
        }

    def speak(self, text: Any) -> tuple[int, bytes | dict[str, Any]]:
        """Turn a short reply into WAV audio (the browser falls back to its own voice)."""
        if self.synth is None:
            return HTTPStatus.NOT_IMPLEMENTED, {"error": "tts_not_configured"}
        if not isinstance(text, str) or not text.strip():
            return HTTPStatus.BAD_REQUEST, {"error": "missing_text"}
        if len(text) > MAX_SPEAK_CHARS * 4:
            return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "text_too_long"}
        cleaned = clean_for_speech(text, max_chars=MAX_SPEAK_CHARS)
        if not cleaned:
            return HTTPStatus.BAD_REQUEST, {"error": "nothing_to_say"}
        try:
            return HTTPStatus.OK, self.synth.synthesize(cleaned, language=self.config.language)
        except TTSError as exc:
            return HTTPStatus.BAD_GATEWAY, {"error": str(exc)}

    # -- server-side confirmation ---------------------------------------------
    # A bare ``confirmed: true`` proves nothing. The server hands out a short-lived,
    # single-use token bound to the exact goal; it is only accepted back once.

    def _sign(self, nonce: str, expires: int, goal: str) -> str:
        msg = f"{nonce}.{expires}.{hashlib.sha256(goal.encode()).hexdigest()}".encode()
        return hmac.new(self._confirm_key, msg, hashlib.sha256).hexdigest()

    def issue_confirmation(self, goal: str) -> str:
        nonce = secrets.token_hex(8)
        expires = int(time.time()) + CONFIRM_TTL_SECONDS
        return f"{nonce}.{expires}.{self._sign(nonce, expires, goal)}"

    def consume_confirmation(self, token: Any, goal: str) -> bool:
        """Validate and spend a token atomically (the HTTP server is multi-threaded)."""
        if not isinstance(token, str) or token.count(".") != 2:
            return False
        nonce, raw_expires, signature = token.split(".")
        try:
            expires = int(raw_expires)
        except ValueError:
            return False
        if not hmac.compare_digest(signature, self._sign(nonce, expires, goal)):
            return False
        now = time.time()
        with self._lock:
            self._spent = {n: t for n, t in self._spent.items() if t > now}
            if expires < now or nonce in self._spent:
                return False
            self._spent[nonce] = expires
            return True

    def check_pin(self, supplied: Any) -> tuple[bool, int]:
        """Constant-time PIN check with lockout. Returns (ok, http_status_if_not_ok)."""
        with self._lock:
            now = time.time()
            if now < self._pin_locked_until:
                return False, HTTPStatus.TOO_MANY_REQUESTS
            ok = isinstance(supplied, str) and hmac.compare_digest(
                supplied.encode(), (self.approval_pin or "").encode()
            )
            if ok:
                self._pin_failures = 0
                return True, HTTPStatus.OK
            self._pin_failures += 1
            if self._pin_failures >= PIN_MAX_FAILURES:
                self._pin_failures = 0
                self._pin_locked_until = now + PIN_LOCK_SECONDS
            return False, HTTPStatus.FORBIDDEN

    async def execute(
        self, goal: str, confirm_token: Any = None, approval_pin: Any = None
    ) -> tuple[int, dict[str, Any]]:
        cfg = self.config
        description = self._describe(goal)
        if description["kind"] != IntentKind.TASK.value:
            return HTTPStatus.BAD_REQUEST, {"error": "not_a_task", **description}
        if description["needs_confirmation"]:
            if self.production and not self.approval_pin:
                return HTTPStatus.FORBIDDEN, {"error": "approval_not_configured", **description}
            if not isinstance(confirm_token, str) or not confirm_token:
                return HTTPStatus.CONFLICT, {
                    "error": "confirmation_required",
                    "confirm_token": self.issue_confirmation(description["goal"]),
                    "needs_pin": bool(self.approval_pin),
                    **description,
                }
            if self.approval_pin:
                ok, status = self.check_pin(approval_pin)
                if not ok:
                    error = "too_many_attempts" if status == 429 else "invalid_pin"
                    return status, {"error": error}
            if not self.consume_confirmation(confirm_token, description["goal"]):
                return HTTPStatus.CONFLICT, {
                    "error": "confirmation_required",
                    "confirm_token": self.issue_confirmation(description["goal"]),
                    "needs_pin": bool(self.approval_pin),
                    **description,
                }
        if self.current_task:
            try:
                running = await self.client.get_task(self.current_task)
            except ArtemisClientError:
                running = None
            if running is not None and not running.done:
                return HTTPStatus.CONFLICT, {"error": "busy", "task_id": self.current_task}
        profile = cfg.sensitive_profile if description["sensitive"] else cfg.profile
        try:
            handle = await self.client.submit(
                description["goal"], profile=profile, device_serial=cfg.device_serial
            )
        except (ArtemisClientError, ValueError) as exc:
            return HTTPStatus.BAD_GATEWAY, {"error": str(exc)}
        self.current_task = handle.task_id
        return HTTPStatus.OK, {"task_id": handle.task_id, "profile": profile}

    async def task_status(self, task_id: str) -> tuple[int, dict[str, Any]]:
        try:
            result = await self.client.get_task(task_id)
        except ArtemisClientError as exc:
            return HTTPStatus.BAD_GATEWAY, {"error": str(exc)}
        return HTTPStatus.OK, {
            "task_id": task_id,
            "status": result.status,
            "done": result.done,
            "succeeded": result.succeeded,
            "error": result.error,
            "output": None if result.output is None else str(result.output)[:500],
        }

    async def stop(self, task_id: str | None) -> tuple[int, dict[str, Any]]:
        target = task_id or self.current_task
        if not target:
            return HTTPStatus.OK, {"stopped": False, "detail": "no running task"}
        try:
            stopped = await self.client.stop(target)
        except ArtemisClientError as exc:
            return HTTPStatus.BAD_GATEWAY, {"error": str(exc)}
        return HTTPStatus.OK, {"stopped": bool(stopped), "task_id": target}


def _make_handler(app: VoiceWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "artemis-voice"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep logs quiet
            return

        # ---- helpers ----
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _guard(self, *, write: bool) -> bool:
            if not app.host_allowed(self.headers.get("Host")):
                self._send(HTTPStatus.FORBIDDEN, {"error": "host_not_allowed"})
                return False
            if not app.token_ok(self.headers.get("Authorization")):
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "bad_token"})
                return False
            if write:
                origin = self.headers.get("Origin")
                if (
                    origin
                    and urlparse(origin).netloc.lower() != (self.headers.get("Host") or "").lower()
                ):
                    self._send(HTTPStatus.FORBIDDEN, {"error": "cross_origin"})
                    return False
                if self.headers.get(CSRF_HEADER) != "1":
                    self._send(HTTPStatus.FORBIDDEN, {"error": "missing_csrf_header"})
                    return False
            return True

        def _read(self, limit: int) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > limit:
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"})
                return None
            return self.rfile.read(length)

        def _json_body(self) -> dict[str, Any] | None:
            raw = self._read(MAX_JSON_BYTES)
            if raw is None:
                return None
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return None
            if not isinstance(data, dict):
                self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return None
            return data

        # ---- routes ----
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                if not app.host_allowed(self.headers.get("Host")):
                    self._send(HTTPStatus.FORBIDDEN, {"error": "host_not_allowed"})
                    return
                page = resources.files("artemis_voice.web").joinpath("index.html").read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(page)
                return
            if not self._guard(write=False):
                return
            if parsed.path == "/api/task":
                task_id = (parse_qs(parsed.query).get("id") or [""])[0]
                if not task_id:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": "missing_id"})
                    return
                status, payload = asyncio.run(app.task_status(task_id))
                self._send(status, payload)
            elif parsed.path == "/api/health":
                self._send(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "stt": getattr(app.stt, "name", "stt"),
                        "tts": app.synth is not None,
                    },
                )
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._guard(write=True):
                return
            if path == "/api/transcribe":
                raw = self._read(MAX_WAV_BYTES)
                if raw is None:
                    return
                status, payload = app.transcribe(raw)
            elif path == "/api/execute":
                data = self._json_body()
                if data is None:
                    return
                goal = data.get("goal")
                if not isinstance(goal, str) or not goal.strip():
                    self._send(HTTPStatus.BAD_REQUEST, {"error": "missing_goal"})
                    return
                status, payload = asyncio.run(
                    app.execute(goal, data.get("confirm_token"), data.get("approval_pin"))
                )
            elif path == "/api/speak":
                data = self._json_body()
                if data is None:
                    return
                status, result = app.speak(data.get("text"))
                if isinstance(result, bytes):
                    self._send_bytes(status, result, "audio/wav")
                    return
                payload = result
            elif path == "/api/stop":
                data = self._json_body()
                if data is None:
                    return
                task_id = data.get("task_id")
                status, payload = asyncio.run(
                    app.stop(task_id if isinstance(task_id, str) else None)
                )
            else:
                status, payload = HTTPStatus.NOT_FOUND, {"error": "not_found"}
            self._send(status, payload)

    return Handler


def serve(
    app: VoiceWebApp,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create the server. Refuses a non-loopback bind without a token."""
    if not is_loopback(host) and not app.token:
        raise InsecureEndpointError(
            f"Refusing to listen on {host}: pass --token (and --allowed-host) for non-loopback binds."
        )
    return ThreadingHTTPServer((host, port), _make_handler(app))
