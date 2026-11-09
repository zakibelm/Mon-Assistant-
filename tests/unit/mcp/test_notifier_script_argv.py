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

"""ScriptNotifier runs without a shell by default."""

import os
import subprocess
import sys

import pytest

from mcp_server.notifiers.script import ScriptNotifier, build_argv

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX argv splitting")

HOSTILE = 'x\'; touch {marker}; $(touch {marker}) `touch {marker}` "$HOME"'


def test_build_argv_keeps_values_as_single_arguments():
    argv = build_argv(
        "notify --title '{title}' --msg {message}", {"title": "a b; c", "message": "$(x)"}
    )
    assert argv == ["notify", "--title", "a b; c", "--msg", "$(x)"]


def test_default_mode_never_uses_a_shell(tmp_path, monkeypatch):
    marker = tmp_path / "pwned"
    out = tmp_path / "out.txt"
    script = f"import sys; open({str(out)!r}, 'w').write(sys.argv[1])"
    monkeypatch.setenv("ARTEMIS_NOTIFY_CMD", f'{sys.executable} -c "{script}" {{message}}')
    monkeypatch.delenv("ARTEMIS_NOTIFY_SHELL", raising=False)
    hostile = HOSTILE.format(marker=marker)
    assert ScriptNotifier().notify("c1", hostile) is True
    assert not marker.exists()
    assert out.read_text() == hostile


def test_shell_mode_is_opt_in_and_still_escapes(tmp_path, monkeypatch):
    marker = tmp_path / "pwned"
    monkeypatch.setenv("ARTEMIS_NOTIFY_CMD", "echo {message} | cat")
    monkeypatch.setenv("ARTEMIS_NOTIFY_SHELL", "1")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))
    assert ScriptNotifier().notify("c1", HOSTILE.format(marker=marker)) is True
    cmd, kw = calls[0]
    assert kw["shell"] is True and isinstance(cmd, str)
    subprocess.run(cmd, shell=True, check=True)
    assert not marker.exists()
