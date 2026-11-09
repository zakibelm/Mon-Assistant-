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

"""Where utterances come from: a microphone, WAV files, or typed text."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from artemis_voice.audio.vad import UtteranceCollector
from artemis_voice.audio.wav import read_wav
from artemis_voice.config import VoiceConfig
from artemis_voice.errors import AudioError


class AudioSource(Protocol):
    """Blocking source of utterances.

    ``listen`` returns ``(pcm, sample_rate)`` for the next utterance, or
    ``None`` when the stream has ended. It is run in a worker thread.
    """

    def listen(self) -> tuple[bytes, int] | None: ...


class FileSource:
    """Each WAV file is one utterance (useful for tests and recorded notes)."""

    def __init__(self, paths: Iterable[str]) -> None:
        self._paths = list(paths)

    def listen(self) -> tuple[bytes, int] | None:
        if not self._paths:
            return None
        return read_wav(self._paths.pop(0))


def feed_unless_muted(
    collector: UtteranceCollector, frame: bytes, muted: Callable[[], bool] | None
) -> bool:
    """Feed a frame to the collector, discarding it while the assistant is talking.

    Without this the microphone would record the assistant's own voice and its
    spoken "oui ou non" could be heard back as an answer.
    """
    if muted is not None and muted():
        collector.reset()
        return False
    return collector.feed(frame)


class MicrophoneSource:
    """Live microphone with voice-activity detection (needs ``sounddevice``)."""

    def __init__(
        self,
        config: VoiceConfig,
        *,
        device: int | str | None = None,
        mute_while: Callable[[], bool] | None = None,
    ) -> None:
        self._mute_while = mute_while
        try:
            import sounddevice  # noqa: F401
        except (ImportError, OSError) as exc:  # OSError: PortAudio library missing
            raise AudioError(
                "Microphone capture needs sounddevice and PortAudio: "
                "pip install 'artemis-voice[mic]' (and apt install libportaudio2 on Linux). "
                "On a tablet without a microphone stack, use `artemis-voice serve` instead."
            ) from exc
        self._config = config
        self._device = device
        self._calibrated = False

    def listen(self) -> tuple[bytes, int] | None:  # pragma: no cover - needs hardware
        import sounddevice as sd

        cfg = self._config
        block = cfg.sample_rate * cfg.frame_ms // 1000
        collector = UtteranceCollector(
            sample_rate=cfg.sample_rate,
            frame_ms=cfg.frame_ms,
            energy_threshold=cfg.energy_threshold,
            silence_ms=cfg.silence_ms,
            min_utterance_ms=cfg.min_utterance_ms,
            max_utterance_s=cfg.max_utterance_s,
        )
        with sd.RawInputStream(
            samplerate=cfg.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=block,
            device=self._device,
        ) as stream:
            if not self._calibrated:
                collector.calibrate([bytes(stream.read(block)[0]) for _ in range(10)])
                self._calibrated = True
            while True:
                data, _overflowed = stream.read(block)
                if feed_unless_muted(collector, bytes(data), self._mute_while):
                    return collector.pcm, cfg.sample_rate


class TextSource:
    """Typed commands, for hosts with no microphone. Yields text, not audio."""

    def __init__(self, lines: Iterable[str]) -> None:
        self._lines = iter(lines)

    def next_text(self) -> str | None:
        return next(self._lines, None)
