"""Scan public files; private markers must live outside the public tree.

Only categories and filenames are printed, never matched values.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|whsec|rk)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"Bearer [A-Za-z0-9._\-]{16,}"),
)
PATH_PATTERNS = (re.compile(r"[A-Za-z]:\\Users\\"), re.compile(r"[A-Za-z]:/Users/"))
ALLOWED_URL_HOSTS = {
    "modelcontextprotocol.io", "www.python.org", "github.com", "docs.github.com",
    "opensource.org", "spdx.org", "example.invalid", "example.com",
    "cursor.com", "docs.x.ai", "git-lfs.github.com",
}
# Reviewed synthetic transport-denial inputs; never a blanket test exemption.
# URLs are fixed non-secret fixtures. Key/private-marker rules still apply.
FIXTURE_URLS = {
    "https://[::1]/fixture", "https://2130706433/fixture", "https://10.0.0.1/fixture",
    "https://127.1/fixture", "https://127.0.0.2/fixture", "https://0x7f000001/fixture",
    "https://localhost./fixture", "https://sub.localhost/fixture", "https://[fe80::1%25eth0]/fixture",
    "https://example.invalid\\evil/fixture", "https://example.invalid\\\\evil/fixture",
}


def reviewed_url_fixture(name: str, url: str) -> bool:
    return PurePosixPath(name).parts[-2:] in {
        ("tests", "test_p0_hardening.py"),
        ("tests", "test_independent_security_contract.py"),
        ("scripts", "scan_release.py"),
    } and url.replace("\\\\", "\\") in FIXTURE_URLS


URL_RE = re.compile(r'https?://[^\s)>"\']+')
SKIP_PARTS = {".git", ".venv", "__pycache__", "dist", "build", ".pytest_cache"}
PRIVATE_PARTS = {"opensource", "_private_release_audit", "private-markers.json"}


def load_markers(path: Path | None, root: Path) -> tuple[str, ...]:
    if path is None:
        return ()
    if path.resolve().is_relative_to(root.resolve()):
        raise ValueError("marker_file_must_be_outside_public_root")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("marker_file_unreadable") from exc
    if not isinstance(data, list) or not data or not all(isinstance(s, str) and s.strip() for s in data):
        raise ValueError("marker_file_invalid")
    return tuple(data)


def _private(name: str) -> bool:
    return any(part.lower() in PRIVATE_PARTS for part in PurePosixPath(name).parts)


def _link(path: Path) -> bool:
    attrs = path.lstat()
    return path.is_symlink() or bool(getattr(attrs, "st_file_attributes", 0) & 0x400)


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _inspect(name: str, text: str, markers: tuple[str, ...] = ()) -> list[str]:
    candidates = [text]
    if name.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return [f"invalid-python {name}"]
        candidates.extend(value for node in ast.walk(tree) if (value := _literal(node)) is not None)
    found = []
    if any(marker.casefold() in value.casefold() for marker in markers for value in [name, *candidates]):
        found.append(f"private-marker {name}")
    if any(pattern.search(value) for pattern in SECRET_PATTERNS for value in candidates):
        found.append(f"secret-shape {name}")
    if any(pattern.search(value) for pattern in PATH_PATTERNS for value in candidates):
        found.append(f"user-path {name}")
    for value in candidates:
        for url in URL_RE.findall(value):
            try:
                host = urlsplit(url).hostname
            except ValueError:
                host = None
            if host not in ALLOWED_URL_HOSTS and not reviewed_url_fixture(name, url):
                found.append(f"url {name}")
                return found
    return found


def _bytes(name: str, raw: bytes, markers: tuple[str, ...]) -> list[str]:
    if b"\x00" in raw:
        return [f"binary {name}"]
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return [f"non-utf8 {name}"]
    return _inspect(name, text, markers)


def _scan_tree(root: Path, markers: tuple[str, ...] = ()) -> list[str]:
    if not root.is_dir():
        return ["missing-root"]
    found: list[str] = []
    for current, directories, files in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if _private(relative):
                found.append(f"private-path {relative}")
                directories.remove(name)
            elif name in SKIP_PARTS or name.endswith(".egg-info"):
                directories.remove(name)
            elif _link(path):
                found.append(f"symlink {relative}")
                directories.remove(name)
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if _private(relative):
                found.append(f"private-path {relative}")
            elif _link(path):
                found.append(f"symlink {relative}")
            else:
                found.extend(_bytes(relative, path.read_bytes(), markers))
    return found


def _archive_name(name: str) -> str | None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", str(path)):
        return "unsafe-path"
    if _private(str(path)):
        return "private-path"
    if any(part in SKIP_PARTS for part in path.parts) or name.endswith(".pyc"):
        return "packaged-cache"
    return None


def _scan_archive(path: Path, markers: tuple[str, ...] = ()) -> list[str]:
    found: list[str] = []
    if not path.is_file():
        return [f"missing-archive {path.name}"]
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    problem = _archive_name(info.filename)
                    if problem:
                        found.append(f"{problem} {info.filename}")
                    elif stat.S_ISLNK(info.external_attr >> 16):
                        found.append(f"symlink {info.filename}")
                    elif not info.is_dir():
                        found.extend(_bytes(info.filename, archive.read(info), markers))
        else:
            with tarfile.open(path, "r:*") as archive:
                for info in archive:
                    problem = _archive_name(info.name)
                    if problem:
                        found.append(f"{problem} {info.name}")
                    elif info.isdir():
                        continue
                    elif not info.isfile():
                        found.append(f"special-file {info.name}")
                    else:
                        stream = archive.extractfile(info)
                        assert stream is not None
                        found.extend(_bytes(info.name, stream.read(), markers))
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile):
        found.append(f"unreadable-archive {path.name}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--markers-file", type=Path, help="External JSON array; never bundle it")
    args = parser.parse_args(argv)
    try:
        markers = load_markers(args.markers_file, args.root)
        findings = _scan_tree(args.root, markers)
        for archive in args.archive:
            findings.extend(_scan_archive(archive, markers))
    except (OSError, ValueError) as exc:
        print("SCAN_ERROR " + (str(exc) if isinstance(exc, ValueError) else "unreadable-input"))
        return 1
    if findings:
        print("SCAN_FINDINGS " + str(len(findings)))
        print("\n".join(findings))
        return 1
    print("SCAN_CLEAN" + (" (external markers included)" if markers else " (generic rules only)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
