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

"""Voice control for Artemis: speak a command, Whisper transcribes, Artemis executes."""

from artemis_voice.config import VoiceConfig
from artemis_voice.errors import (
    AudioError,
    InsecureEndpointError,
    SpeechRecognitionError,
    VoiceError,
)
from artemis_voice.intent import Intent, IntentKind, parse_confirmation, parse_intent
from artemis_voice.listener import AudioListener, Listener, StdinListener, TextListener
from artemis_voice.session import ConsoleResponder, TurnResult, VoiceAssistant

__version__ = "0.1.0"

__all__ = [
    "AudioError",
    "AudioListener",
    "ConsoleResponder",
    "InsecureEndpointError",
    "Intent",
    "IntentKind",
    "Listener",
    "SpeechRecognitionError",
    "StdinListener",
    "TextListener",
    "TurnResult",
    "VoiceAssistant",
    "VoiceConfig",
    "VoiceError",
    "__version__",
    "parse_confirmation",
    "parse_intent",
]
