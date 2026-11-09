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

"""Turn a transcript into an intent: task, stop, exit, or nothing.

Everything here is deterministic and offline. Matching is accent- and
case-insensitive and covers French, English and Arabic/darija keywords.
The sensitive-action list is a heuristic safety net, not a guarantee: it
only decides whether to ask for a spoken confirmation and to use the more
careful Artemis profile. It is extendable via ``extra_sensitive_words``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class IntentKind(str, Enum):
    EMPTY = "empty"
    TASK = "task"
    STOP = "stop"
    EXIT = "exit"


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    goal: str = ""
    sensitive: bool = False
    heard_wake_word: bool = False
    raw: str = ""


def _fold_char(char: str) -> str:
    lowered = char.lower()
    if len(lowered) != 1:
        lowered = lowered[0]
    decomposed = unicodedata.normalize("NFD", lowered)
    return decomposed[0] if decomposed else lowered


def fold(text: str) -> str:
    """Lower-case and strip accents while keeping the string length (1:1)."""
    return "".join(_fold_char(c) for c in text)


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", fold(text))


def _normalized(text: str) -> str:
    return " ".join(tokens(text))


_STOP_TOKENS = frozenset(
    {
        "stop", "stoppe", "stoppez", "arrete", "arreter", "arretez", "annule", "annuler",
        "cancel", "abort", "halt", "tais", "toi", "ca", "suffit", "laisse", "tomber",
        "tout", "safi", "وقف", "توقف", "الغ", "الغاء",
    }
)  # fmt: skip

_EXIT_PHRASES = frozenset(
    {
        "quitter", "quit", "exit", "au revoir", "goodbye", "bye", "bye bye", "a plus",
        "termine la session", "ferme l assistant", "close assistant",
    }
)  # fmt: skip

_YES_TOKENS = frozenset(
    {
        "oui", "yes", "yeah", "yep", "ouais", "ok", "okay", "confirme", "confirmer", "confirm",
        "go", "vas", "y", "d", "accord", "affirmatif", "valide", "c", "est", "bon",
        "absolument", "exactement", "wakha", "wah", "نعم", "ايه", "اه", "ايوه", "ايوة",
    }
)  # fmt: skip

_NO_TOKENS = _STOP_TOKENS | frozenset({"non", "no", "nope", "nan", "negatif", "jamais", "لا"})

_SENSITIVE_WORDS = frozenset(
    {
        # French
        "payer", "paye", "payez", "paiement", "paiements", "virement", "virements", "virer",
        "transferer", "transfere", "transfert", "acheter", "achete", "achat", "achats",
        "commander", "envoyer", "envoie", "envoyez", "envoi", "publier", "publie", "poster",
        "supprimer", "supprime", "supprimez", "effacer", "efface", "effacez", "vider",
        "desinstaller", "desinstalle", "formater", "reinitialiser", "reinitialise",
        "partager", "partage", "appeler", "appelle", "reserver", "reserve", "soumettre",
        "bancaire", "banque", "pin",
        # English
        "pay", "paying", "payment", "transfer", "send", "sending", "buy", "purchase",
        "order", "checkout", "delete", "remove", "erase", "wipe", "uninstall", "reset",
        "format", "share", "call", "post", "publish", "submit", "book", "password", "bank",
        # Arabic / darija
        "دفع", "ادفع", "ارسل", "ارسال", "حذف", "احذف", "امسح", "مسح", "شراء", "اشتري",
        "تحويل", "حول", "اتصل",
    }
)  # fmt: skip

_SENSITIVE_PHRASES = (
    "mot de passe",
    "carte bancaire",
    "carte de credit",
    "credit card",
    "factory reset",
    "كلمة السر",
    "كلمه السر",
)

_WAKE_PREFIXES = r"(?:(?:hey|ok|okay|salut|dis|he|yo|hi|hello|bonjour)\W+)?"


def is_sensitive(text: str, extra_words: tuple[str, ...] = ()) -> bool:
    """True if the command probably spends money, sends/deletes data or calls."""
    toks = tokens(text)
    extras = {w for word in extra_words for w in tokens(word)}
    if any(t in _SENSITIVE_WORDS or t in extras for t in toks):
        return True
    padded = f" {' '.join(toks)} "
    return any(f" {_normalized(p)} " in padded for p in _SENSITIVE_PHRASES)


def strip_wake_word(text: str, wake_words: tuple[str, ...]) -> tuple[str, bool]:
    """Remove a leading wake word. Returns ``(remaining_text, heard)``."""
    folded = fold(text)
    alternatives = "|".join(re.escape(fold(w)) for w in wake_words if w.strip())
    if not alternatives:
        return text.strip(), False
    match = re.match(rf"^\W*{_WAKE_PREFIXES}(?:{alternatives})\b\W*", folded)
    if not match:
        return text.strip(), False
    return text[match.end() :].strip(), True


def parse_intent(
    text: str,
    *,
    wake_words: tuple[str, ...] = (),
    require_wake_word: bool = False,
    extra_sensitive_words: tuple[str, ...] = (),
) -> Intent:
    """Classify a transcript.

    STOP and EXIT are always honoured, even when a wake word is required, so
    the user can always interrupt the phone.
    """
    raw = text.strip()
    remaining, heard = strip_wake_word(raw, wake_words)
    norm = _normalized(remaining)
    if not norm:
        return Intent(IntentKind.EMPTY, heard_wake_word=heard, raw=raw)

    if norm in _EXIT_PHRASES:
        return Intent(IntentKind.EXIT, heard_wake_word=heard, raw=raw)
    if all(t in _STOP_TOKENS for t in norm.split()):
        return Intent(IntentKind.STOP, heard_wake_word=heard, raw=raw)
    if all(t in _YES_TOKENS or t in _NO_TOKENS for t in norm.split()):
        # A lone "oui" / "non" / "ok" outside a confirmation is never a phone task.
        return Intent(IntentKind.EMPTY, heard_wake_word=heard, raw=raw)

    if require_wake_word and not heard:
        return Intent(IntentKind.EMPTY, raw=raw)
    return Intent(
        IntentKind.TASK,
        goal=remaining,
        sensitive=is_sensitive(remaining, extra_sensitive_words),
        heard_wake_word=heard,
        raw=raw,
    )


def parse_confirmation(text: str) -> bool | None:
    """Interpret a spoken answer: True=yes, False=no, None=unclear.

    A "yes" must be the *whole* utterance, so "oui mais envoie-le à Paul"
    is unclear rather than a confirmation. Any "no" wins.
    """
    toks = tokens(text)
    if not toks:
        return None
    if any(t in _NO_TOKENS for t in toks):
        return False
    if all(t in _YES_TOKENS for t in toks):
        return True
    return None
