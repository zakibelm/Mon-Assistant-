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

"""Speak through a system text-to-speech command (free, offline, no dependency).

The text is sent on the command's *standard input* (or substituted into an
argument that contains ``{text}``), never through a shell, so spoken text can
neither inject commands nor be mistaken for an option.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any

from artemis_voice.errors import TTSError

_WINDOWS_SAPI = (
    "Add-Type -AssemblyName System.Speech;"
    "$t=[Console]::In.ReadToEnd();"
    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($t)"
)
_SAY_VOICES = {"fr": "Thomas", "fr-ca": "Amélie", "en": "Samantha", "ar": "Majed"}


def default_command(
    language: str | None = None,
    *,
    voice: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
    os_name: str | None = None,
) -> list[str] | None:
    """Best available system TTS for this machine, or ``None``."""
    lang = (language or "fr").lower().replace("_", "-")
    short = lang.split("-")[0]
    platform = platform or sys.platform
    os_name = os_name or os.name
    if platform == "darwin" and which("say"):
        chosen = voice or _SAY_VOICES.get(lang) or _SAY_VOICES.get(short)
        return ["say", "-v", chosen] if chosen else ["say"]
    if os_name == "nt":
        powershell = which("powershell") or which("pwsh")
        if powershell:
            return [powershell, "-NoProfile", "-Command", _WINDOWS_SAPI]
    for binary in ("espeak-ng", "espeak"):
        if which(binary):
            return [binary, "-v", voice or short]
    if which("spd-say"):
        return ["spd-say", "-e", "-l", short]
    return None


class CommandTTS:
    name = "command"

    def __init__(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        if not argv:
            raise TTSError("Empty TTS command")
        self.argv = list(argv)
        self.timeout = timeout
        self._popen = popen
        self._proc: Any = None
        self._interrupted = False

    def speak(self, text: str, *, language: str | None = None) -> None:
        use_stdin = not any("{text}" in part for part in self.argv)
        argv = [
            part.replace("{text}", text).replace("{lang}", (language or "").split("-")[0])
            for part in self.argv
        ]
        self._interrupted = False
        try:
            proc = self._popen(
                argv,
                stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise TTSError(f"Cannot start TTS command {argv[0]!r}: {exc}") from exc
        self._proc = proc
        try:
            proc.communicate(text.encode("utf-8") if use_stdin else None, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise TTSError(f"TTS command {argv[0]!r} timed out") from exc
        finally:
            self._proc = None
        if proc.returncode != 0 and not self._interrupted:
            raise TTSError(f"TTS command {argv[0]!r} exited with {proc.returncode}")

    def interrupt(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._interrupted = True
            proc.terminate()
