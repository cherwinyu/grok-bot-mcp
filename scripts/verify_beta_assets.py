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
import re
import sys
import tomllib
import zipfile

from scripts.build_release import public_files


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


def verify(root: Path, wheelhouse: Path) -> dict:
    root, wheelhouse = root.absolute(), wheelhouse.absolute()
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
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
        for name in expected_source:
            if archive.read(name) != (root / name).read_bytes():
                raise CandidateError("source_archive_content_mismatch")

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
        "package_source_members_checked": checked_source, "installed_exact_release_wheel": True,
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
