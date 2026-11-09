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

"""Custom Script / Command Hook Notifier for universal IDE and editor integration."""

import logging
import os
import re
import shlex
import subprocess
from typing import Any

from mcp_server.notifiers.base import BaseNotifier

logger = logging.getLogger("mcp_server.notifiers.script")

_PLACEHOLDER = re.compile(r"\{(title|message|conversation_id|event_type|trace_id)\}")


def _escape_for_context(value: str, quote: str | None) -> str:
    """Make ``value`` inert in the shell quoting context where it is inserted."""
    if os.name == "nt":
        # cmd.exe has no reliable escape inside quotes: neutralise the characters
        # that can end a quoted string or expand variables.
        safe = value.replace('"', "'").replace("%", "%%")
        return safe if quote else f'"{safe}"'
    if quote == "'":
        return value.replace("'", "'\\''")
    if quote == '"':
        return re.sub(r'(["\\$`])', r"\\\1", value)
    return shlex.quote(value)


def render_command(template: str, values: dict[str, str]) -> str:
    """Substitute placeholders without letting values break out of the command.

    Event text (task titles/messages) can contain screen content or model output, so
    it must never be able to inject shell syntax. Each value is escaped for the quote
    context it lands in (single, double or unquoted), so existing templates such as
    ``--title '{title}'`` keep working.
    """
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(template):
        ch = template[i]
        match = _PLACEHOLDER.match(template, i)
        if match:
            out.append(_escape_for_context(str(values.get(match.group(1), "")), quote))
            i = match.end()
            continue
        if ch == "\\" and quote != "'" and i + 1 < len(template) and os.name != "nt":
            out.append(template[i : i + 2])
            i += 2
            continue
        if ch in "'\"" and (quote is None or quote == ch):
            quote = None if quote == ch else ch
        out.append(ch)
        i += 1
    return "".join(out)


def build_argv(template: str, values: dict[str, str]) -> list[str]:
    """Split the template into arguments, then fill placeholders inside each argument.

    No shell is involved, so values are plain data: quotes, ``;``, ``$()`` or
    backticks in event text have no special meaning. Shell syntax in the template
    itself (pipes, redirections) is NOT interpreted; set ``ARTEMIS_NOTIFY_SHELL=1``
    to opt in to the legacy shell mode.
    """
    parts = shlex.split(template, posix=os.name != "nt")
    return [_PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), part) for part in parts]


class ScriptNotifier(BaseNotifier):
    """Notifier that executes a user-defined command or script when an event occurs.

    This adapter enables universal integration with any custom IDE, editor (Neovim/Emacs),
    or automation platform by allowing users to define ARTEMIS_NOTIFY_CMD or MCP_NOTIFY_COMMAND.
    Placeholders like {title}, {message}, {conversation_id}, {event_type}, and {trace_id}
    are automatically replaced before execution.
    """

    ENV_VARS = [
        "ARTEMIS_NOTIFY_CMD",
        "MCP_NOTIFY_COMMAND",
    ]

    @property
    def name(self) -> str:
        return "script"

    def _get_command_template(self) -> str | None:
        for var in self.ENV_VARS:
            val = os.getenv(var)
            if val and val.strip():
                return val.strip()
        return None

    def is_available(self) -> bool:
        return self._get_command_template() is not None

    def notify(
        self,
        conversation_id: str,
        message: str,
        title: str | None = None,
        event_type: str = "completed",
        payload: dict[str, Any] | None = None,
    ) -> bool:
        cmd_template = self._get_command_template()
        if not cmd_template:
            return False

        trace_id = (payload or {}).get("trace_id", "")
        formatted_title = title or f"Artemis Task {event_type.capitalize()}"

        # Default: argv without a shell. Legacy shell mode (opt-in) escapes values per quote context.
        try:
            values = {
                "title": formatted_title,
                "message": message,
                "conversation_id": conversation_id,
                "event_type": event_type,
                "trace_id": trace_id,
            }
            use_shell = (
                os.getenv("ARTEMIS_NOTIFY_SHELL", "").strip()
                in {
                    "1",
                    "true",
                    "yes",
                }
                and os.getenv("ARTEMIS_ENV", "").lower() != "production"
            )
            cmd: str | list[str] = (
                render_command(cmd_template, values)
                if use_shell
                else build_argv(cmd_template, values)
            )
            subprocess.run(
                cmd,
                shell=use_shell,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            logger.info("Custom script notification command executed")
            return True
        except Exception as e:
            logger.warning(f"Failed to execute custom script notification: {e}")
            return False
