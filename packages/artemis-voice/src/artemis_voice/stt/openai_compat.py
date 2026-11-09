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

"""Hosted Whisper via any OpenAI-compatible ``/audio/transcriptions`` endpoint.

Works with Groq (free tier), OpenAI, or a self-hosted server. It is opt-in
because it uploads the recorded audio to a third party; the local provider
keeps voice data on the device.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from artemis_voice.audio.wav import pcm_to_wav
from artemis_voice.errors import SpeechRecognitionError
from artemis_voice.stt.base import Transcript

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def build_multipart(fields: dict[str, str], filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n'
        ).encode()
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class OpenAICompatSTT:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str = GROQ_BASE_URL,
        model: str = "whisper-large-v3-turbo",
        api_key: str | None = None,
        api_key_env: str = "GROQ_API_KEY",
        language: str | None = None,
        vocabulary: tuple[str, ...] = (),
        timeout: float = 60.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get(api_key_env)
        self.api_key_env = api_key_env
        self.language = language
        self.prompt = ", ".join(vocabulary) if vocabulary else None
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        if not self.api_key:
            raise SpeechRecognitionError(
                f"No API key: set {self.api_key_env} or pass --stt-api-key."
            )
        fields = {"model": self.model, "response_format": "verbose_json", "temperature": "0"}
        if self.language:
            fields["language"] = self.language
        if self.prompt:
            fields["prompt"] = self.prompt
        body, content_type = build_multipart(fields, "speech.wav", pcm_to_wav(pcm, sample_rate))
        request = urllib.request.Request(
            f"{self.base_url}/audio/transcriptions",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": content_type},
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SpeechRecognitionError(f"STT API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SpeechRecognitionError(f"STT API request failed: {exc}") from exc

        segments = payload.get("segments") or []
        no_speech = max((float(s.get("no_speech_prob", 0.0)) for s in segments), default=0.0)
        return Transcript(
            text=str(payload.get("text", "")).strip(),
            language=payload.get("language"),
            no_speech_prob=no_speech,
        )
