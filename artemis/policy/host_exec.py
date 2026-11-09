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

"""Best-effort screen for code the image-processor model writes and the host then executes.

That code runs in a Jupyter kernel on the host, outside the phone driver, so the Policy Engine
cannot see what it does. A kernel that can spawn processes or open sockets could drive ``adb``
directly. This module narrows what such code may touch: a short allowlist of imports and a
denylist of names that give access to processes, the interpreter or the network.

It is a *filter*, not a sandbox: Python cannot be sandboxed from inside Python. Treat it as
defence in depth against prompt-injected generated code; the structural fix is to run the kernel
in a container with no adb socket and no network (see docs/POLICY-ENGINE.md).
"""

import ast

ALLOWED_MODULES = frozenset(
    {
        "math", "cmath", "numpy", "cv2", "PIL", "json", "re", "itertools", "collections",
        "functools", "typing", "dataclasses", "colorsys", "base64", "io", "random",
        "statistics", "textwrap", "enum", "fractions", "decimal", "string", "operator",
        "copy", "pathlib", "glob", "__future__",
    }
)  # fmt: skip
ALLOWED_EXACT = frozenset({"artemis.utils.cv_canvas"})

FORBIDDEN_NAMES = frozenset(
    {
        "os", "sys", "subprocess", "system", "popen", "Popen", "posix_spawn", "spawn", "spawnl",
        "spawnv", "fork", "forkpty", "execv", "execve", "execl", "execvp", "exec_command",
        "run_command", "importlib", "runpy", "pty", "ctypes", "socket", "builtins",
        "distutils", "__import__", "__builtins__", "__globals__", "__subclasses__", "__class__",
        "__bases__", "__mro__", "__dict__", "__code__", "__loader__", "__spec__", "__getattribute__",
    }
)  # fmt: skip
# Builtins that are dangerous as bare names but legitimate as methods (``Image.open``,
# ``re.compile``), so they are only refused when called directly.
FORBIDDEN_BUILTINS = frozenset(
    {
        "eval", "exec", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr",
        "breakpoint", "input", "open", "memoryview", "help",
    }
)  # fmt: skip


def check_python_source(code: str) -> str | None:
    """Return a refusal reason, or ``None`` if the code stays inside the allowed surface."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # the executor reports syntax errors itself
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_ok(alias.name):
                    return f"import of {alias.name!r} is not allowed in generated code"
        elif isinstance(node, ast.ImportFrom):
            if node.level or not _module_ok(node.module or ""):
                return f"import from {node.module!r} is not allowed in generated code"
        elif isinstance(node, ast.Name) and (
            node.id in FORBIDDEN_NAMES or node.id in FORBIDDEN_BUILTINS
        ):
            return f"use of {node.id!r} is not allowed in generated code"
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            return f"access to attribute {node.attr!r} is not allowed in generated code"
        elif isinstance(node, ast.alias) and node.name in FORBIDDEN_NAMES:
            return f"import of {node.name!r} is not allowed in generated code"
    return None


def _module_ok(name: str) -> bool:
    return name in ALLOWED_EXACT or name.split(".")[0] in ALLOWED_MODULES
