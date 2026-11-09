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

"""Minimal WAV helpers (mono, 16-bit PCM) built on the standard library."""

from __future__ import annotations

import array
import io
import sys
import wave

from artemis_voice.errors import AudioError

MAX_WAV_BYTES = 25 * 1024 * 1024


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def wav_bytes_to_pcm(data: bytes) -> tuple[bytes, int]:
    """Decode WAV bytes into mono int16 PCM. Returns ``(pcm, sample_rate)``."""
    if len(data) > MAX_WAV_BYTES:
        raise AudioError(f"Audio too large ({len(data)} bytes, max {MAX_WAV_BYTES}).")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"Not a valid PCM WAV file: {exc}") from exc
    if width != 2:
        raise AudioError(f"Only 16-bit WAV is supported (got {width * 8}-bit).")
    if channels == 1:
        return frames, rate
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    mono = array.array(
        "h",
        (
            sum(samples[i : i + channels]) // channels
            for i in range(0, len(samples) - channels + 1, channels)
        ),
    )
    if sys.byteorder == "big":
        mono.byteswap()
    return mono.tobytes(), rate


def read_wav(path: str) -> tuple[bytes, int]:
    try:
        with open(path, "rb") as handle:
            return wav_bytes_to_pcm(handle.read(MAX_WAV_BYTES + 1))
    except OSError as exc:
        raise AudioError(f"Cannot read {path}: {exc}") from exc
