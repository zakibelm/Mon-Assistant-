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

"""Text-to-speech providers behind one interface."""

from __future__ import annotations

import shlex
from typing import Any

from artemis_voice.errors import TTSError
from artemis_voice.tts.base import Synthesizer, TextToSpeech, clean_for_speech
from artemis_voice.tts.command import CommandTTS, default_command
from artemis_voice.tts.synth import (
    OpenAICompatSynth,
    PiperSynth,
    SynthesizingTTS,
    WavPlayer,
)

PROVIDERS = ("auto", "none", "command", "piper", "openai")


def create_synthesizer(provider: str, **kwargs: Any) -> Synthesizer:
    """Providers that produce audio bytes (usable by the web page too)."""
    if provider == "piper":
        return PiperSynth(**kwargs)
    if provider == "openai":
        return OpenAICompatSynth(**kwargs)
    raise TTSError(f"{provider!r} does not produce audio bytes; use piper or openai")


def create_tts(
    provider: str,
    *,
    language: str | None = None,
    voice: str | None = None,
    command: str | None = None,
    player: str | None = None,
    synth_options: dict[str, Any] | None = None,
) -> TextToSpeech | None:
    """Build the speaker for a provider name; ``None`` means silent.

    ``auto`` picks the system voice when one exists. ``command`` takes a command
    line (split like a shell would, but never run through one).
    """
    if provider == "none":
        return None
    if provider in {"auto", "command"}:
        argv = shlex.split(command) if command else default_command(language, voice=voice)
        if not argv:
            if provider == "auto":
                return None
            raise TTSError("No system TTS found; install espeak-ng or pass --tts-command.")
        return CommandTTS(argv)
    synth = create_synthesizer(provider, **(synth_options or {}))
    return SynthesizingTTS(synth, WavPlayer(shlex.split(player) if player else None))


__all__ = [
    "PROVIDERS",
    "CommandTTS",
    "OpenAICompatSynth",
    "PiperSynth",
    "Synthesizer",
    "SynthesizingTTS",
    "TextToSpeech",
    "WavPlayer",
    "clean_for_speech",
    "create_synthesizer",
    "create_tts",
    "default_command",
]
