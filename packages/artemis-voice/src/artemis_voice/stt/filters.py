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

"""Drop Whisper's well-known hallucinations on silence/noise.

Whisper tends to invent captions ("Thanks for watching", "Sous-titrage ...")
when fed near-silence. Letting those through would submit nonsense tasks to a
real phone, so they are discarded before intent parsing.
"""

from __future__ import annotations

import re

from artemis_voice.intent import fold

_KNOWN = frozenset(
    {
        "thanks for watching",
        "thank you for watching",
        "thank you",
        "thanks",
        "you",
        "bye",
        "merci",
        "merci d avoir regarde cette video",
        "merci d avoir regarde",
        "sous titrage st 501",
        "sous titres realises par la communaute d amara org",
        "sous titrage societe radio canada",
        "subtitles by the amara org community",
        "please subscribe",
        "n oubliez pas de vous abonner",
        "transcribed by",
        "اشتركوا في القناة",
        "ترجمة نانسي قنقر",
    }
)  # fmt: skip

_MARKERS = ("amara org", "sous titr", "subtitles by", "transcribed by", "ترجمه")


def is_hallucination(text: str) -> bool:
    norm = " ".join(re.findall(r"\w+", fold(text)))
    if len(norm) < 2:
        return True
    if norm in _KNOWN or any(marker in norm for marker in _MARKERS):
        return True
    words = norm.split()
    # "ha ha ha ha ha ..." / repeated single token loops
    return len(words) >= 4 and len(set(words)) == 1
