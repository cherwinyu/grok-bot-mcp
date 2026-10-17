"""Build from an explicit public file list, then scan before exporting artifacts."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

if __package__:
    from .scan_release import _link, _scan_archive, _scan_tree, load_markers
    from .reproducible_archives import canonicalize_release
else:
    from scan_release import _link, _scan_archive, _scan_tree, load_markers
    from reproducible_archives import canonicalize_release

ROOT_FILES = (
    ".gitattributes", ".gitignore", "LICENSE", "MANIFEST.in", "pyproject.toml",
    "README.md", "README.zh-CN.md", "NOTICE.md", "SECURITY.md", "CONTRIBUTING.md",
    "CHANGELOG.md", "RELEASE_NOTES.md", "AGENTS.md", "ROADMAP.md", "CODE_OF_CONDUCT.md",
)
DIRECTORIES = {
    "src": {".py", ".md"}, "tests": {".py"}, "docs": {".md"}, "routines": {".md"},
    "scripts": {".py", ".ps1"}, ".github": {".yml", ".yaml", ".md", ""},
    "examples": {".md", ".py", ".gd", ".godot", ".json"},
}
DENIED_PREFIXES = ("docs/reports/", "docs/p0/", "tests/fixtures/")
DENIED_FILES = {
    "scripts/replay_regressions.py",
    "scripts/replay_onboarding_review.py",
    "scripts/verify_test_preservation.py",
}


def public_files(root: Path) -> list[Path]:
    files = [root / name for name in ROOT_FILES]
    for directory, extensions in DIRECTORIES.items():
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or any(p.endswith(".egg-info") for p in relative.parts):
                continue
            if _link(path):
                raise ValueError("public_source_contains_link")
            if path.is_file():
                relative_name = relative.as_posix()
                if relative_name in DENIED_FILES or relative_name.startswith(DENIED_PREFIXES):
                    raise ValueError("forbidden_public_file: " + relative_name)
                if path.suffix not in extensions:
                    raise ValueError("unexpected_public_file: " + relative_name)
                files.append(path)
    if any(not path.is_file() or _link(path) for path in files):
        raise ValueError("public_source_missing_or_linked")
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markers-file", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    markers = load_markers(args.markers_file, root)
    findings = _scan_tree(root, markers)
    if findings:
        print("\n".join(findings))
        return 1
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with tempfile.TemporaryDirectory(prefix="bridge-release-") as temporary:
        stage = Path(temporary) / "source"
        outputs = Path(temporary) / "artifacts"
        outputs.mkdir()
        for path in public_files(root):
            target = stage / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        source_zip = outputs / f"grokbot-mcp-bridge-{version}-source.zip"
        with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
        # The build frontend builds the wheel from the sdist as well.
        subprocess.run([sys.executable, "-m", "build", str(stage), "--outdir", str(outputs)], check=True)
        canonicalize_release(outputs)
        artifacts = sorted(outputs.iterdir())
        if len(artifacts) != 3:
            raise ValueError("unexpected_artifact_count")
        for artifact in artifacts:
            findings = _scan_archive(artifact, markers)
            if findings:
                print("\n".join(findings))
                return 1
        checksums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in artifacts)
        (outputs / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
        destination = root / "dist"
        destination.mkdir(exist_ok=True)
        for artifact in outputs.iterdir():
            shutil.copyfile(artifact, destination / artifact.name)
    print("RELEASE_BUILT_AND_SCANNED " + version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
