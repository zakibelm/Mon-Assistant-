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

"""Synthesizers that return WAV bytes (Piper, OpenAI-compatible API) and a WAV player."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from artemis_voice.errors import TTSError
from artemis_voice.tts.base import Synthesizer


class PiperSynth:
    """Local neural voices through the ``piper`` binary (free, offline).

    Check the options of your Piper version; this uses the classic CLI:
    ``piper --model voice.onnx --output_file out.wav`` with the text on stdin.
    """

    name = "piper"

    def __init__(
        self,
        model: str,
        *,
        binary: str = "piper",
        timeout: float = 60.0,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.model, self.binary, self.timeout, self._run = model, binary, timeout, runner

    def synthesize(self, text: str, *, language: str | None = None) -> bytes:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            proc = self._run(
                [self.binary, "--model", self.model, "--output_file", path],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout,
            )
            if proc.returncode != 0:
                raise TTSError(f"piper exited with {proc.returncode}")
            with open(path, "rb") as handle:
                data = handle.read()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TTSError(f"piper failed: {exc}") from exc
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if not data:
            raise TTSError("piper produced no audio")
        return data


class OpenAICompatSynth:
    """Hosted TTS via an OpenAI-compatible ``/audio/speech`` endpoint (opt-in).

    The text of the replies is sent to the provider. Available voices and
    languages depend on the provider; check them before relying on French.
    """

    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "tts-1",
        voice: str = "alloy",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout: float = 60.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model, self.voice, self.timeout = model, voice, timeout
        self.api_key = api_key or os.environ.get(api_key_env)
        self.api_key_env = api_key_env
        self._open = opener or urllib.request.urlopen

    def synthesize(self, text: str, *, language: str | None = None) -> bytes:
        if not self.api_key:
            raise TTSError(f"No API key: set {self.api_key_env} or pass --tts-api-key.")
        body = json.dumps(
            {"model": self.model, "voice": self.voice, "input": text, "response_format": "wav"}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/audio/speech",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            raise TTSError(f"TTS API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TTSError(f"TTS API request failed: {exc}") from exc
        if not data:
            raise TTSError("TTS API returned no audio")
        return data


def default_player(
    *,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
    os_name: str | None = None,
) -> list[str] | None:
    """Command that plays a WAV file; ``{file}`` is replaced by the path."""
    platform, os_name = platform or sys.platform, os_name or os.name
    if platform == "darwin" and which("afplay"):
        return ["afplay", "{file}"]
    if os_name == "nt":
        powershell = which("powershell") or which("pwsh")
        if powershell:
            return [
                powershell,
                "-NoProfile",
                "-Command",
                "(New-Object Media.SoundPlayer $args[0]).PlaySync()",
                "{file}",
            ]
    if which("paplay"):
        return ["paplay", "{file}"]
    if which("aplay"):
        return ["aplay", "-q", "{file}"]
    if which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "{file}"]
    return None


class WavPlayer:
    def __init__(
        self,
        argv: Sequence[str] | None = None,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        chosen = list(argv) if argv else default_player()
        if not chosen:
            raise TTSError("No audio player found (afplay/paplay/aplay/ffplay). Use --player.")
        self.argv, self._popen = chosen, popen
        self._proc: Any = None
        self._interrupted = False

    def play(self, wav: bytes) -> None:
        fd, path = tempfile.mkstemp(suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(wav)
            self._interrupted = False
            try:
                proc = self._popen(
                    [part.replace("{file}", path) for part in self.argv],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                raise TTSError(f"Cannot start audio player {self.argv[0]!r}: {exc}") from exc
            self._proc = proc
            proc.wait()
            if proc.returncode != 0 and not self._interrupted:
                raise TTSError(f"Audio player exited with {proc.returncode}")
        finally:
            self._proc = None
            try:
                os.unlink(path)
            except OSError:
                pass

    def interrupt(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._interrupted = True
            proc.terminate()


class SynthesizingTTS:
    """A :class:`Synthesizer` plus a :class:`WavPlayer` behaves like any TTS."""

    def __init__(self, synthesizer: Synthesizer, player: WavPlayer) -> None:
        self.synthesizer, self.player = synthesizer, player
        self.name = synthesizer.name

    def speak(self, text: str, *, language: str | None = None) -> None:
        self.player.play(self.synthesizer.synthesize(text, language=language))

    def interrupt(self) -> None:
        self.player.interrupt()
