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

"""Speech-to-text providers behind one interface."""

from __future__ import annotations

from typing import Any

from artemis_voice.errors import SpeechRecognitionError
from artemis_voice.stt.base import SpeechToText, Transcript
from artemis_voice.stt.faster_whisper_stt import FasterWhisperSTT
from artemis_voice.stt.filters import is_hallucination
from artemis_voice.stt.openai_compat import OpenAICompatSTT

PROVIDERS = ("faster-whisper", "openai")


def create_stt(provider: str, **kwargs: Any) -> SpeechToText:
    """Build a provider by name. Add new engines here, nothing else changes."""
    if provider == "faster-whisper":
        return FasterWhisperSTT(**kwargs)
    if provider == "openai":
        return OpenAICompatSTT(**kwargs)
    raise SpeechRecognitionError(f"Unknown STT provider {provider!r}; choose from {PROVIDERS}")


__all__ = [
    "PROVIDERS",
    "FasterWhisperSTT",
    "OpenAICompatSTT",
    "SpeechToText",
    "Transcript",
    "create_stt",
    "is_hallucination",
]
