"""Verify the exact audited candidate files and installed-wheel identity.

Offline, read-only. This script never uploads, sends a webhook or approves release.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path

if __package__:
    from .source_version import source_version
else:
    try:
        from source_version import source_version
    except ModuleNotFoundError:
        from scripts.source_version import source_version
import re
import sys
import tarfile
import zipfile

if __package__:
    from .build_release import public_files
else:
    from build_release import public_files


class CandidateError(ValueError):
    pass


SUM_LINE = re.compile(r"^([0-9a-f]{64})  ([a-zA-Z0-9][a-zA-Z0-9_.-]+)$")


def checksums(raw: bytes) -> dict[str, str]:
    if len(raw) > 32768 or not raw.endswith(b"\n"):
        raise CandidateError("invalid_checksum_manifest")
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        raise CandidateError("invalid_checksum_manifest") from None
    entries = {}
    for line in text.splitlines():
        match = SUM_LINE.fullmatch(line)
        if not match or match.group(2) in entries:
            raise CandidateError("invalid_checksum_manifest")
        entries[match.group(2)] = match.group(1)
    if not entries:
        raise CandidateError("invalid_checksum_manifest")
    return entries


def record_members(archive: zipfile.ZipFile) -> int:
    members = [x.filename for x in archive.infolist() if not x.is_dir()]
    if len(set(members)) != len(members):
        raise CandidateError("duplicate_wheel_member")
    names = set(members)
    records = [name for name in names if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise CandidateError("wheel_record_missing")
    record = records[0]
    try:
        rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf-8"))))
    except (UnicodeError, KeyError, csv.Error):
        raise CandidateError("wheel_record_invalid") from None
    checked = set()
    for row in rows:
        if len(row) != 3 or row[0] not in names or row[0] in checked:
            raise CandidateError("wheel_record_invalid")
        name, value, size = row
        checked.add(name)
        if name == record:
            if value or size:
                raise CandidateError("wheel_record_invalid")
            continue
        raw = archive.read(name)
        hashed = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")
        if value != "sha256=" + hashed or size != str(len(raw)):
            raise CandidateError("wheel_record_mismatch")
    if checked != names:
        raise CandidateError("wheel_record_incomplete")
    return len(checked)



SDIST_GENERATED = frozenset({
    "PKG-INFO", "setup.cfg",
    "src/grokbot_mcp_bridge.egg-info/PKG-INFO",
    "src/grokbot_mcp_bridge.egg-info/SOURCES.txt",
    "src/grokbot_mcp_bridge.egg-info/dependency_links.txt",
    "src/grokbot_mcp_bridge.egg-info/entry_points.txt",
    "src/grokbot_mcp_bridge.egg-info/requires.txt",
    "src/grokbot_mcp_bridge.egg-info/top_level.txt",
})
# Setuptools emits CODEOWNERS in the reviewed source ZIP but not in the sdist.
SDIST_SOURCE_EXCLUSIONS = frozenset({".github/CODEOWNERS"})


def sdist_members(raw: bytes, source_files: dict[str, bytes], version: str) -> int:
    """Prove that every shipped sdist source file matches the audited source ZIP.

    Only reviewed setuptools-generated metadata and a known CODEOWNERS omission
    are accepted. This is source identity checking, not a license/privacy approval.
    """
    if len(raw) > 64 * 1024 * 1024:
        raise CandidateError("sdist_limit_exceeded")
    prefix = f"grokbot_mcp_bridge-{version}/"
    expected = set(source_files) - SDIST_SOURCE_EXCLUSIONS
    seen: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for item in archive:
                # setuptools places one root directory named without a slash.
                if item.isdir() and item.name == prefix[:-1]:
                    continue
                if not item.name.startswith(prefix):
                    raise CandidateError("sdist_path_invalid")
                name = item.name[len(prefix):].rstrip("/") if item.isdir() else item.name[len(prefix):]
                if not name or any(part in {"", ".", ".."} for part in name.split("/")):
                    raise CandidateError("sdist_path_invalid")
                if item.isdir():
                    continue
                if not item.isfile():
                    raise CandidateError("sdist_special_member")
                if name in seen:
                    raise CandidateError("sdist_duplicate_member")
                if item.size > 4 * 1024 * 1024 or total + item.size > 64 * 1024 * 1024:
                    raise CandidateError("sdist_limit_exceeded")
                member = archive.extractfile(item)
                if member is None:
                    raise CandidateError("sdist_member_unreadable")
                data = member.read(item.size + 1)
                if len(data) != item.size:
                    raise CandidateError("sdist_member_unreadable")
                seen[name] = data
                total += len(data)
    except (OSError, EOFError, tarfile.TarError):
        raise CandidateError("sdist_unreadable") from None
    if set(seen) != expected | SDIST_GENERATED:
        raise CandidateError("sdist_source_file_set_mismatch")
    for name in expected:
        if seen[name] != source_files[name]:
            raise CandidateError("sdist_source_content_mismatch")
    return len(expected)


def verify(root: Path, wheelhouse: Path) -> dict:
    root, wheelhouse = root.absolute(), wheelhouse.absolute()
    # Source identity cannot be established from an installed-only sandbox.
    if not (root / "src/grokbot_mcp/__init__.py").is_file():
        raise CandidateError("source_tree_missing")
    version = source_version(root)
    basename = "grokbot_mcp_bridge-" + version
    wheel_name = basename + "-py3-none-any.whl"
    sdist_name = basename + ".tar.gz"
    source_name = "grokbot-mcp-bridge-" + version + "-source.zip"
    expected = {wheel_name, sdist_name, source_name}
    dist = root / "dist"
    actual = {p.name for p in dist.iterdir()}
    if actual != expected | {"SHA256SUMS.txt"}:
        raise CandidateError("release_file_set_mismatch")
    listed = checksums((dist / "SHA256SUMS.txt").read_bytes())
    if set(listed) != expected:
        raise CandidateError("release_manifest_file_set_mismatch")
    for name in expected:
        if hashlib.sha256((dist / name).read_bytes()).hexdigest() != listed[name]:
            raise CandidateError("release_checksum_mismatch")
    selected = wheelhouse / wheel_name
    if not selected.is_file() or selected.read_bytes() != (dist / wheel_name).read_bytes():
        raise CandidateError("installed_wheel_differs_from_release")
    if (root / "src/grokbot_mcp/__init__.py").read_text(encoding="utf-8").count(version) != 1:
        raise CandidateError("source_version_mismatch")

    with zipfile.ZipFile(dist / source_name) as archive:
        if len(set(archive.namelist())) != len(archive.namelist()):
            raise CandidateError("duplicate_source_archive_member")
        expected_source = {p.relative_to(root).as_posix() for p in public_files(root)}
        if set(archive.namelist()) != expected_source:
            raise CandidateError("source_archive_manifest_mismatch")
        source_files = {name: archive.read(name) for name in expected_source}
        for name, content in source_files.items():
            if content != (root / name).read_bytes():
                raise CandidateError("source_archive_content_mismatch")

    sdist_count = sdist_members((dist / sdist_name).read_bytes(), source_files, version)

    with zipfile.ZipFile(dist / wheel_name) as archive:
        count = record_members(archive)
        metadata = [x for x in archive.namelist() if x.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise CandidateError("wheel_metadata_invalid")
        content = archive.read(metadata[0]).decode("utf-8").splitlines()
        if ("Version: " + version) not in content or "Name: grokbot-mcp-bridge" not in content:
            raise CandidateError("wheel_metadata_mismatch")
        checked_source = 0
        for name in archive.namelist():
            if name.startswith("grokbot_mcp/") and name.endswith((".py", ".md")):
                source = root / "src" / name
                if not source.is_file() or archive.read(name) != source.read_bytes():
                    raise CandidateError("wheel_source_content_mismatch")
                checked_source += 1
        if checked_source < 40:
            raise CandidateError("wheel_source_scope_incomplete")
    return {
        "ok": True, "version": version, "release_wheel": wheel_name,
        "wheel_sha256": listed[wheel_name], "source_sha256": listed[source_name],
        "sdist_sha256": listed[sdist_name], "wheel_record_rows": count,
        "package_source_members_checked": checked_source, "sdist_source_members_checked": sdist_count,
        "installed_exact_release_wheel": True,
        "publication_authorized": False, "public_release_created": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify(args.root, args.wheelhouse)
        print(json.dumps(report, indent=2))
        return 0
    except (CandidateError, OSError, ValueError, zipfile.BadZipFile) as exc:
        code = str(exc) if isinstance(exc, CandidateError) else "candidate_unreadable"
        print(json.dumps({"ok": False, "code": code, "publication_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
