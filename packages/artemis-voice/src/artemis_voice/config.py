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

"""Runtime configuration for the voice assistant."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WAKE_WORDS: tuple[str, ...] = ("artemis", "arthemis", "artemisse")

# Words that help Whisper spell app/UI names correctly.
DEFAULT_VOCABULARY: tuple[str, ...] = (
    "Artemis",
    "Android",
    "WhatsApp",
    "Wi-Fi",
    "Bluetooth",
    "Paramètres",
    "Settings",
    "Chrome",
    "Gmail",
)


@dataclass(frozen=True)
class VoiceConfig:
    """Settings shared by the live assistant and the web push-to-talk server."""

    base_url: str = "http://127.0.0.1:8000"
    token: str | None = None
    allow_insecure: bool = False
    device_serial: str | None = None

    # Artemis execution profiles. Sensitive commands run on the safer profile.
    profile: str = "flash"
    sensitive_profile: str = "pro"
    task_timeout: float = 600.0

    # Speech handling.
    language: str | None = None  # None = auto-detect (fr / en / ar ...)
    wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS
    require_wake_word: bool = False
    vocabulary: tuple[str, ...] = DEFAULT_VOCABULARY

    # Safety.
    confirm_sensitive: bool = True
    extra_sensitive_words: tuple[str, ...] = ()
    confirm_retries: int = 1

    # Audio capture / voice-activity detection.
    sample_rate: int = 16000
    frame_ms: int = 30
    energy_threshold: float = 500.0  # int16 RMS; raised automatically above noise
    silence_ms: int = 900
    min_utterance_ms: int = 300
    max_utterance_s: float = 15.0
