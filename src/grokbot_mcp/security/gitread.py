"""Bounded read-only Git queries; no index refresh, fsmonitor or hooks."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path


def run_git(root: Path, args: list[str], runner=subprocess.run):
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
               "-c", "core.quotePath=false", "-C", str(root), *args]
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        return runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                      env=env, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(command, 1, "", "")
