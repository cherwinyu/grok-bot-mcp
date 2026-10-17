"""Fictional deployment fixtures. Fake executables are inspected, never launched."""
from __future__ import annotations
from pathlib import Path
import json
import unittest
from grokbot_mcp import __version__
from tests import test_reliability as reliability


def fake_install(root: Path, version=__version__) -> Path:
    python = root / "Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"FICTIONAL METADATA FIXTURE - NEVER EXECUTE\n")
    (root / "pyvenv.cfg").write_text("version = 3.12.10\ninclude-system-site-packages = false\n", encoding="utf-8")
    site = root / "Lib/site-packages"
    package = site / "grokbot_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "' + version + '"\n', encoding="utf-8")
    (package / "server.py").write_text("# fictional server fixture; never execute\n", encoding="utf-8")
    for name, v in (("grokbot_mcp_bridge", version), ("mcp", "2.3.0")):
        metadata = site / (name + "-" + v + ".dist-info/METADATA")
        metadata.parent.mkdir()
        metadata.write_text("Name: " + name.replace("_", "-") + "\nVersion: " + v + "\n", encoding="utf-8")
    server = site / "mcp/server/__init__.py"
    server.parent.mkdir(parents=True)
    server.write_text("# fictional MCP fixture\n", encoding="utf-8")
    return python


class DeploymentCase(unittest.TestCase):
    def setUp(self):
        self.case = reliability.ReliabilityTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.registry = self.case.root, self.case.registry
        self.base = self.registry.parent

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def files(self, root=None):
        root = root or self.root / ".bridge"
        return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
                for p in root.rglob("*") if p.is_file() and p.name != ".submit.lock"}
