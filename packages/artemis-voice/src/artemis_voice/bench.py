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

"""Measure real speech-to-text quality instead of trusting marketing claims.

Put ``name.wav`` next to ``name.txt`` (the exact words you said), then run
``artemis-voice bench --audio-dir dir --models tiny,base,small``. Each model
gets a word-error-rate (lower is better) and a latency figure, on *your*
voice, language mix and microphone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from artemis_voice.audio.wav import read_wav
from artemis_voice.intent import tokens
from artemis_voice.stt.base import SpeechToText


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate on accent/case-folded tokens (Levenshtein / len(ref))."""
    ref, hyp = tokens(reference), tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


@dataclass
class BenchRow:
    name: str
    reference: str
    hypothesis: str
    wer: float
    seconds: float
    audio_seconds: float


@dataclass
class BenchResult:
    provider: str
    rows: list[BenchRow] = field(default_factory=list)

    @property
    def mean_wer(self) -> float:
        return sum(r.wer for r in self.rows) / len(self.rows) if self.rows else 0.0

    @property
    def realtime_factor(self) -> float:
        """Processing time / audio time. Below 1.0 means faster than real time."""
        audio = sum(r.audio_seconds for r in self.rows)
        return sum(r.seconds for r in self.rows) / audio if audio else 0.0


def load_cases(directory: str) -> list[tuple[Path, str]]:
    cases = []
    for wav in sorted(Path(directory).glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if ref.is_file():
            cases.append((wav, ref.read_text(encoding="utf-8").strip()))
    return cases


def run_benchmark(stt: SpeechToText, cases: list[tuple[Path, str]]) -> BenchResult:
    result = BenchResult(provider=getattr(stt, "name", "stt"))
    for wav, reference in cases:
        pcm, rate = read_wav(str(wav))
        started = time.perf_counter()
        hypothesis = stt.transcribe(pcm, rate).text
        elapsed = time.perf_counter() - started
        result.rows.append(
            BenchRow(
                name=wav.name,
                reference=reference,
                hypothesis=hypothesis,
                wer=wer(reference, hypothesis),
                seconds=elapsed,
                audio_seconds=len(pcm) / 2 / rate,
            )
        )
    return result
