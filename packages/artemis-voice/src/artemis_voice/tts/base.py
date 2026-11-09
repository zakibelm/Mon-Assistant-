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

"""Provider-neutral text-to-speech interfaces and text preparation."""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol, runtime_checkable


@runtime_checkable
class TextToSpeech(Protocol):
    """Speaks text on the host's speakers. ``speak`` blocks until done."""

    name: str

    def speak(self, text: str, *, language: str | None = None) -> None: ...

    def interrupt(self) -> None:
        """Stop the current utterance as soon as possible (safe from any thread)."""
        ...


@runtime_checkable
class Synthesizer(Protocol):
    """Turns text into WAV bytes (used by players and by the web page)."""

    name: str

    def synthesize(self, text: str, *, language: str | None = None) -> bytes: ...


_DROPPED_CATEGORIES = {"So", "Sk", "Cs", "Co", "Cn", "Cf"}


def clean_for_speech(text: str, max_chars: int = 300) -> str:
    """Make a status message pleasant and safe to read aloud.

    Drops emoji/pictographs and quotation marks, flattens whitespace, removes
    leading dashes (so a text can never look like a command-line option) and
    truncates at a word boundary.
    """
    kept = []
    for char in text:
        if char in '«»"“”':
            continue
        if char in "\n\r\t":
            kept.append(" ")
        elif (
            unicodedata.category(char) in _DROPPED_CATEGORIES or unicodedata.category(char) == "Cc"
        ):
            continue
        else:
            kept.append(char)
    cleaned = re.sub(r"\s+", " ", "".join(kept).replace("…", ".")).strip()
    cleaned = cleaned.lstrip("-–—•*#> ").strip()
    if len(cleaned) > max_chars:
        cut = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(",;:- ")
        cleaned = cut + "."
    return cleaned
