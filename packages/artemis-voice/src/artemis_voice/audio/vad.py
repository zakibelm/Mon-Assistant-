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

"""Energy-based voice-activity detection (pure Python, no dependencies)."""

from __future__ import annotations

import array
import math
import sys
from collections import deque


def rms(frame: bytes) -> float:
    """Root-mean-square level of an int16 mono frame."""
    if len(frame) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class UtteranceCollector:
    """Feed fixed-size frames; ``feed`` returns True once one utterance is complete.

    Speech starts after two consecutive loud frames (a short pre-roll is kept
    so the first syllable is not clipped) and ends after ``silence_ms`` of
    quiet or ``max_utterance_s`` of audio. Utterances shorter than
    ``min_utterance_ms`` are discarded as clicks/noise.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        energy_threshold: float = 500.0,
        silence_ms: int = 900,
        min_utterance_ms: int = 300,
        max_utterance_s: float = 15.0,
        preroll_ms: int = 300,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.threshold = float(energy_threshold)
        self._silence_frames = max(1, silence_ms // frame_ms)
        self._min_frames = max(1, min_utterance_ms // frame_ms)
        self._max_frames = max(1, int(max_utterance_s * 1000 // frame_ms))
        self._preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // frame_ms))
        self._chunks: list[bytes] = []
        self._speaking = False
        self._loud_run = 0
        self._quiet_run = 0
        self._speech_frames = 0
        self._noise: list[float] = []

    def calibrate(self, frames: list[bytes]) -> None:
        """Raise the threshold above the measured ambient noise floor."""
        levels = sorted(rms(f) for f in frames if f)
        if levels:
            median = levels[len(levels) // 2]
            self.threshold = max(self.threshold, median * 3.0)

    def reset(self) -> None:
        self._preroll.clear()
        self._chunks = []
        self._speaking = False
        self._loud_run = self._quiet_run = self._speech_frames = 0

    @property
    def pcm(self) -> bytes:
        return b"".join(self._chunks)

    def feed(self, frame: bytes) -> bool:
        loud = rms(frame) >= self.threshold
        if not self._speaking:
            self._preroll.append(frame)
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= 2:
                self._speaking = True
                self._chunks = list(self._preroll)
                self._speech_frames = self._loud_run
                self._quiet_run = 0
            return False

        self._chunks.append(frame)
        if loud:
            self._speech_frames += 1
            self._quiet_run = 0
        else:
            self._quiet_run += 1
        if self._quiet_run >= self._silence_frames or len(self._chunks) >= self._max_frames:
            if self._speech_frames >= self._min_frames:
                return True
            self.reset()  # too short: treat as noise and keep listening
        return False
