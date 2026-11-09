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

"""Local Whisper through ``faster-whisper`` (free, offline, audio stays on-device)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from artemis_voice.errors import SpeechRecognitionError
from artemis_voice.stt.base import Transcript

_TARGET_RATE = 16000


class FasterWhisperSTT:
    name = "faster-whisper"

    def __init__(
        self,
        model: str = "small",
        *,
        device: str = "auto",
        compute_type: str = "int8",
        language: str | None = None,
        vocabulary: tuple[str, ...] = (),
        beam_size: int = 5,
        min_avg_logprob: float = -1.0,
        no_speech_threshold: float = 0.6,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.prompt = ", ".join(vocabulary) if vocabulary else None
        self.beam_size = beam_size
        self.min_avg_logprob = min_avg_logprob
        self.no_speech_threshold = no_speech_threshold
        self._factory = model_factory
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            if self._factory is not None:
                self._model = self._factory()
            else:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise SpeechRecognitionError(
                        "faster-whisper is not installed: pip install 'artemis-voice[local]'"
                    ) from exc
                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=self.compute_type
                )
        return self._model

    def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        try:
            import numpy as np
        except ImportError as exc:
            raise SpeechRecognitionError("numpy is required: pip install numpy") from exc

        audio = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype=np.int16)
        audio = audio.astype(np.float32) / 32768.0
        if sample_rate != _TARGET_RATE and audio.size:
            target_len = int(audio.size * _TARGET_RATE / sample_rate)
            audio = np.interp(
                np.linspace(0, audio.size - 1, num=max(target_len, 1)),
                np.arange(audio.size),
                audio,
            ).astype(np.float32)

        model = self._load()
        try:
            segments, info = model.transcribe(
                audio,
                language=self.language,
                initial_prompt=self.prompt,
                beam_size=self.beam_size,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            kept: list[str] = []
            worst_no_speech = 0.0
            for seg in segments:
                no_speech = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
                logprob = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
                worst_no_speech = max(worst_no_speech, no_speech)
                if no_speech > self.no_speech_threshold and logprob < self.min_avg_logprob:
                    continue  # confident silence/noise segment
                text = str(seg.text).strip()
                if text:
                    kept.append(text)
        except SpeechRecognitionError:
            raise
        except Exception as exc:  # provider internals vary
            raise SpeechRecognitionError(f"faster-whisper failed: {exc}") from exc

        return Transcript(
            text=" ".join(kept).strip(),
            language=getattr(info, "language", None),
            no_speech_prob=worst_no_speech,
        )
