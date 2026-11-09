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

"""Listeners hide where words come from (audio+Whisper, or typed text)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Protocol

from artemis_voice.audio.sources import AudioSource
from artemis_voice.stt.base import SpeechToText
from artemis_voice.stt.filters import is_hallucination


class Listener(Protocol):
    async def hear(self) -> str | None:
        """Next utterance as text. ``""`` = nothing usable, ``None`` = stream ended."""
        ...


class AudioListener:
    """Audio source -> Whisper -> text, with hallucination filtering."""

    def __init__(self, source: AudioSource, stt: SpeechToText) -> None:
        self.source = source
        self.stt = stt

    async def hear(self) -> str | None:
        item = await asyncio.to_thread(self.source.listen)
        if item is None:
            return None
        pcm, rate = item
        transcript = await asyncio.to_thread(self.stt.transcribe, pcm, rate)
        text = transcript.text.strip()
        return "" if is_hallucination(text) else text


class TextListener:
    """Pre-defined lines, mostly for tests and scripted runs."""

    def __init__(self, lines: Iterable[str]) -> None:
        self._lines = iter(lines)

    async def hear(self) -> str | None:
        return next(self._lines, None)


class StdinListener:
    """Typed commands for hosts without a microphone."""

    def __init__(self, prompt: str = "> ") -> None:
        self.prompt = prompt

    async def hear(self) -> str | None:
        try:
            return await asyncio.to_thread(input, self.prompt)
        except EOFError:
            return None
