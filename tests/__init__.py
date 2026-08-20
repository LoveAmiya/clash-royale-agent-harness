"""Test-package bootstrap for deterministic local artifacts and compatibility imports."""

import sys

from . import support as _support

sys.modules.setdefault("support", _support)
_support.install_test_stubs()
