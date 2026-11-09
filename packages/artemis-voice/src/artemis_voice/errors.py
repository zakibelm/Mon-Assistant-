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

"""Exceptions raised by the voice layer."""


class VoiceError(Exception):
    """Base class for all artemis-voice errors."""


class InsecureEndpointError(VoiceError):
    """The daemon endpoint would carry voice commands over an unsafe channel."""


class SpeechRecognitionError(VoiceError):
    """A speech-to-text provider failed or is not installed/configured."""


class AudioError(VoiceError):
    """Audio could not be captured, decoded or is in an unsupported format."""


class TTSError(VoiceError):
    """A text-to-speech provider failed or is not installed/configured."""
