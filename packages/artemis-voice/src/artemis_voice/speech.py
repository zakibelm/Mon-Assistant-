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

"""Spoken replies: console text plus text-to-speech, without hearing itself."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from artemis_voice.errors import TTSError
from artemis_voice.tts.base import TextToSpeech, clean_for_speech

_STOP = object()


class SpokenResponder:
    """Prints every message and reads it aloud on a background thread.

    * Messages are queued, so a slow voice never blocks listening or tasks.
    * ``interrupt()`` (used by "stop") silences the voice and drops the queue.
    * ``is_speaking`` stays true a short moment after the voice ends, so the
      microphone does not pick up the tail of the assistant's own voice and
      mistake it for a command (e.g. its own "confirmer ? oui ou non").
    * A broken voice disables itself after one warning; replies keep printing.
    """

    def __init__(
        self,
        tts: TextToSpeech,
        *,
        language: str | None = None,
        max_chars: int = 300,
        tail_seconds: float = 0.5,
        max_queue: int = 3,
        printer: Callable[[str], None] | None = None,
    ) -> None:
        self.tts, self.language = tts, language
        self.max_chars, self.tail_seconds = max_chars, tail_seconds
        self._print = printer or (lambda m: print(f"[artemis-voice] {m}", flush=True))
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max(1, max_queue))
        self._lock = threading.Lock()
        self._active = False
        self._quiet_until = 0.0
        self._enabled = True
        self._thread = threading.Thread(target=self._worker, name="artemis-tts", daemon=True)
        self._thread.start()

    # -- Responder API ------------------------------------------------------

    def say(self, message: str, spoken: str | None = None) -> None:
        self._print(message)
        if not self._enabled:
            return
        text = clean_for_speech(spoken if spoken is not None else message, self.max_chars)
        if not text:
            return
        with self._lock:
            self._active = True  # visible to the microphone immediately
        while True:
            try:
                self._queue.put_nowait(text)
                return
            except queue.Full:  # drop the oldest pending line, keep the newest
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def interrupt(self) -> None:
        self._drain()
        self.tts.interrupt()

    # -- state ----------------------------------------------------------------

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._active or time.monotonic() < self._quiet_until

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_speaking:
                return True
            time.sleep(0.02)
        return False

    def close(self) -> None:
        self._drain()
        self.tts.interrupt()
        self._queue.put(_STOP)
        self._thread.join(timeout=2.0)

    # -- internals ----------------------------------------------------------

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._active = False

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                self.tts.speak(str(item), language=self.language)
            except TTSError as exc:
                self._enabled = False
                self._print(f"Voix désactivée : {exc}")
                self._drain()
            finally:
                with self._lock:
                    self._active = not self._queue.empty()
                    self._quiet_until = time.monotonic() + self.tail_seconds
