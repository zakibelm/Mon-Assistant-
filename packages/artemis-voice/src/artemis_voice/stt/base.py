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

"""Provider-neutral speech-to-text interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None
    no_speech_prob: float | None = None


@runtime_checkable
class SpeechToText(Protocol):
    """Anything that turns mono 16-bit PCM audio into text.

    Providers are interchangeable: the assistant only depends on this
    interface, so a different engine can be swapped in without touching
    the rest of the code.
    """

    name: str

    def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript: ...
