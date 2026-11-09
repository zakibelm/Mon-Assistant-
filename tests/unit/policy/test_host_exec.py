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

"""RT-03: code the image-processor model writes must not be able to reach adb or the network.

This screen is best-effort (see ``artemis.policy.host_exec``): these tests pin the escapes we know
about. They do not prove the kernel is sandboxed.
"""

import pytest

from artemis.policy.host_exec import check_python_source
from artemis.utils.python_executor import validate_coder_script

LEGIT = """
from artemis.utils.cv_canvas import ImageCanvas
import cv2, numpy as np, re, glob
from pathlib import Path
from PIL import Image
img = Image.open("a.png")
pattern = re.compile("a+")
print(np.zeros((2, 2)).shape, sorted(glob.glob("*.png")))
"""

ESCAPES = {
    "subprocess": "import subprocess\nsubprocess.run(['adb','shell','input','tap','1','1'])",
    "os import": "import os\nos.system('adb shell reboot')",
    "from os": "from os import system\nsystem('adb devices')",
    "__import__": "__import__('subprocess').run(['adb'])",
    "PIL.os attribute walk": "from PIL import Image\nImage.os.system('adb shell')",
    "numpy.testing.os": "import numpy as np\nnp.testing.os.system('adb')",
    "getattr string building": "getattr(__builtins__, 'ex' + 'ec')('1')",
    "eval": "eval('1+1')",
    "exec": "exec('import os')",
    "open arbitrary file": "open('/home/user/.android/adbkey').read()",
    "socket to adb server": "import socket\nsocket.create_connection(('127.0.0.1', 5037))",
    "ctypes": "import ctypes",
    "importlib": "import importlib\nimportlib.import_module('os')",
    "class walk": "().__class__.__bases__[0].__subclasses__()",
    "artemis internals": "from artemis.drivers.factory import create_driver",
    "relative import": "from . import secrets",
    "pty": "import pty\npty.spawn('sh')",
    "runpy": "import runpy",
    "sys modules": "import sys\nsys.modules['os']",
}


def test_legitimate_image_code_passes():
    assert check_python_source(LEGIT) is None
    assert validate_coder_script(LEGIT) is True


@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_known_escapes_are_refused(name):
    assert check_python_source(ESCAPES[name]), name
    with pytest.raises(ValueError, match="POLICY ERROR"):
        validate_coder_script(ESCAPES[name])


def test_syntax_errors_are_left_to_the_executor():
    assert check_python_source("def (") is None
