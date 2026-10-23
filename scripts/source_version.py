"""Read the literal package version without importing or executing source code."""
from __future__ import annotations

import ast
from importlib import metadata
from pathlib import Path
import tomllib


def source_version(root: Path) -> str:
    try:
        source = (root / "src/grokbot_mcp/__init__.py").read_text(encoding="utf-8")
    except FileNotFoundError:
        # Installed-wheel QA deliberately copies scripts/configuration without src.
        # Preserve static-version compatibility for older standalone bootstrap copies.
        project = root / "pyproject.toml"
        if project.is_file():
            version = tomllib.loads(project.read_text(encoding="utf-8"))["project"].get("version")
            if isinstance(version, str) and version:
                return version
        try:
            return metadata.version("grokbot-mcp-bridge")
        except metadata.PackageNotFoundError:
            raise ValueError("source_version_unavailable") from None
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
            break
    raise ValueError("source_version_invalid")
