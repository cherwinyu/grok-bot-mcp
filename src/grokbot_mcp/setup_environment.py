"""Local runtime checks. No credentials, registry writes, or network access."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from grokbot_mcp import __version__


def interpreter() -> str:
    # Do NOT resolve symlinks: resolving a venv interpreter can select base Python.
    path = Path(sys.executable)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("python_interpreter_invalid")
    return str(path.absolute())


def inspect_environment() -> dict:
    codes = []
    if sys.platform != "win32":
        codes.append("windows_required")
    if sys.version_info[:2] < (3, 12):
        codes.append("python_unsupported")
    try:
        python = interpreter()
    except (OSError, ValueError):
        python = None
        codes.append("python_interpreter_invalid")
    if not shutil.which("git"):
        codes.append("git_missing")
    else:
        try:
            result = subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
            if result.returncode:
                codes.append("git_unavailable")
        except (OSError, subprocess.SubprocessError):
            codes.append("git_unavailable")
    if python:
        # -I ignores PYTHONPATH, activation, and the current source directory.
        probe = (
            "import importlib.metadata as m,grokbot_mcp; "
            "assert m.version('mcp')=='2.3.0'; "
            f"assert m.version('grokbot-mcp-bridge')==grokbot_mcp.__version__=={__version__!r}; "
            "print('BRIDGE_INSTALLATION_OK')"
        )
        try:
            result = subprocess.run([python, "-I", "-c", probe], capture_output=True,
                                    text=True, timeout=20, check=False)
            if result.returncode or result.stdout.strip() != "BRIDGE_INSTALLATION_OK":
                codes.append("installation_mismatch")
        except (OSError, subprocess.SubprocessError):
            codes.append("installation_unavailable")
    return {"ready": not codes, "codes": codes, "python": python,
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "bridge_version": __version__, "isolated_environment": sys.prefix != sys.base_prefix}
