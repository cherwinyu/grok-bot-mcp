"""Native argv execution with bounded timeout cleanup of the owned process tree."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def _kill_tree(process) -> bool:
    try:
        if os.name == "nt":
            # Fixed system utility, not an executable supplied by result text or
            # resolved using the registered command's environment overrides.
            utility = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"
            outcome = subprocess.run([str(utility), "/T", "/F", "/PID", str(process.pid)],
                                     shell=False, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.STDOUT, timeout=10, check=False)
            return outcome.returncode == 0
        os.killpg(process.pid, signal.SIGKILL)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> dict:
    """Like run(..., stdout=PIPE, stderr=STDOUT), retaining PID for tree cleanup.

    subprocess.run kills the immediate child before raising TimeoutExpired and
    does not expose its PID. Popen/communicate lets taskkill see the live parent
    first, then drains the same merged byte stream without decoding in flight.
    """
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    try:
        process = subprocess.Popen(argv, shell=False, cwd=cwd, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, **options)
    except OSError:
        return {"raw": b"", "executed": False, "exit_code": None,
                "timed_out": False, "error": "rerun_start_failed"}
    timed_out = False
    cleanup_ok = True
    drained = True
    raw = b""
    try:
        try:
            raw, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            raw = exc.output or b""
            cleanup_ok = _kill_tree(process)
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    cleanup_ok = False
            try:
                # Bounded even when a surviving descendant holds the pipe open.
                raw, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired as drain:
                raw = drain.output or raw
                cleanup_ok = False
                drained = False
        except BaseException:
            _kill_tree(process)
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            drained = False
            raise
    finally:
        # On Windows communicate uses a reader thread. Closing its buffered
        # stream while a surviving descendant holds the pipe can block forever.
        if drained and process.stdout is not None:
            process.stdout.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cleanup_ok = False
    error = "rerun_tree_cleanup_failed" if not cleanup_ok else "rerun_timeout" if timed_out else None
    return {"raw": raw, "executed": True, "exit_code": process.returncode,
            "timed_out": timed_out, **({"error": error} if error else {})}
