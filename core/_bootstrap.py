"""Bootstrap sys.path so `from lib.X import Y` works from any CWD.

Import this at the top of every core/*.py script:

    import _bootstrap  # noqa: F401  — sys.path setup

The file lives in core/ (where it's also reachable as `from core import
_bootstrap` when the script is invoked as `python -m core.foo`).

This module resolves the project root as the parent of core/ and inserts it
into sys.path. After import, `from lib.X import Y` and `from
rules_catalog.X import Y` work regardless of CWD.
"""
import os as _os
import sys as _sys

_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..'))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
