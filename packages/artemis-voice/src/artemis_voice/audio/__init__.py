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

"""Audio capture, VAD and WAV helpers."""

from artemis_voice.audio.sources import (
    AudioSource,
    FileSource,
    MicrophoneSource,
    TextSource,
    feed_unless_muted,
)
from artemis_voice.audio.vad import UtteranceCollector, rms
from artemis_voice.audio.wav import pcm_to_wav, read_wav, wav_bytes_to_pcm

__all__ = [
    "AudioSource",
    "FileSource",
    "MicrophoneSource",
    "TextSource",
    "UtteranceCollector",
    "pcm_to_wav",
    "read_wav",
    "feed_unless_muted",
    "rms",
    "wav_bytes_to_pcm",
]
