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

"""Event text must never be able to inject shell commands via ARTEMIS_NOTIFY_CMD."""

import os
import subprocess

import pytest

from mcp_server.notifiers.script import ScriptNotifier, render_command

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell quoting")

PAYLOADS = [
    "'; touch {marker}; echo '",
    '"; touch {marker}; echo "',
    "$(touch {marker})",
    "`touch {marker}`",
    "a && touch {marker}",
    "line1\ntouch {marker}",
    "plain text with 'quote' and \"double\" and $HOME",
]
TEMPLATES = [
    "printf %s '{message}' > {out}",
    'printf %s "{message}" > {out}',
    "printf %s {message} > {out}",
]


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("payload", PAYLOADS)
def test_values_cannot_escape_their_quoting_context(tmp_path, template, payload):
    marker = tmp_path / "pwned"
    out = tmp_path / "out.txt"
    message = payload.format(marker=marker)
    cmd = render_command(template.replace("{out}", str(out)), {"message": message})
    subprocess.run(cmd, shell=True, check=True)
    assert not marker.exists(), f"injection succeeded with {cmd!r}"
    assert out.read_text() == message  # value arrives intact


def test_notifier_end_to_end(monkeypatch, tmp_path):
    marker = tmp_path / "pwned"
    out = tmp_path / "out.txt"
    monkeypatch.setenv("ARTEMIS_NOTIFY_SHELL", "1")  # the template uses a redirection
    monkeypatch.setenv("ARTEMIS_NOTIFY_CMD", f"printf '%s|%s' '{{title}}' '{{message}}' > {out}")
    notifier = ScriptNotifier()
    assert notifier.notify("c1", f"x'; touch {marker}; '", title="T it'le")
    assert not marker.exists()
    assert out.read_text() == f"T it'le|x'; touch {marker}; '"
