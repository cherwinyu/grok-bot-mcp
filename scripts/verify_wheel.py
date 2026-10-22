"""Install the built wheel in a new venv and test away from every source tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path


def main() -> int:
    # Windows redirected stdout can default to cp1252; failure paths may be Unicode.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheelhouse, output = args.wheelhouse.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    wheel = next(wheelhouse.glob("grokbot_mcp_bridge-" + version + "-*.whl"))
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "GROKBOT_MCP_REGISTRY"}}
    with tempfile.TemporaryDirectory(prefix="bridge-wheel-") as name:
        base = Path(name)
        venv.EnvBuilder(with_pip=True).create(base / "venv space 测试")
        python = base / "venv space 测试" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), str(wheel)],
                       check=True, env=env)
        sandbox = base / "qa outside 源码"
        sandbox.mkdir()
        for directory in ("tests", "scripts", "routines", "examples"):
            shutil.copytree(root / directory, sandbox / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(root / "pyproject.toml", sandbox / "pyproject.toml")
        for name in ("README.md", "README.zh-CN.md"):
            shutil.copyfile(root / name, sandbox / name)
        check = (
            "import pathlib,sys,importlib.metadata as m,grokbot_mcp; "
            "p=pathlib.Path(grokbot_mcp.__file__).resolve(); "
            "assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()),p; "
            "assert m.version('mcp')=='2.3.0'; "
            f"assert grokbot_mcp.__version__=={version!r}; "
            "print('INSTALLED_WHEEL_IMPORT_VERIFIED')"
        )
        subprocess.run([str(python), "-I", "-c", check], cwd=sandbox, env=env, check=True)
        proc = subprocess.run([str(python), "-I", "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
                              cwd=sandbox, env=env, capture_output=True, text=True, encoding="utf-8", timeout=1200)
        (output / "green.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        print(proc.stdout + proc.stderr)
    # These identify the actual checkout used to build and test this wheel (PRs may
    # use a temporary merge commit; compare its tree with the PR head separately).
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True).strip()
    summary = {"source_commit": commit, "source_tree": tree, "wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
               "version": version, "mcp_version": "2.3.0", "exit_code": proc.returncode,
               "source_tree_imported": False, "qa_accepted": False,
               "scope": "offline protocol/package tests; not live Routine acceptance"}
    (output / "wheel-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
