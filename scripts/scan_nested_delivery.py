"""Bounded recursive ZIP/wheel/tar scan, reusing the release privacy rules.

No archive extraction or payload execution. Private markers remain external.
Binary dependency wheels are outside this bridge-only delivery scan's scope.
"""
from __future__ import annotations
import argparse
import io
from pathlib import Path
import stat
import tarfile
import zipfile
from scripts import scan_release as base

MAX_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_DEPTH = 4


def inspect(raw, name, markers=(), *, depth=0, budget=None):
    budget = [MAX_BYTES, MAX_MEMBERS] if budget is None else budget
    if depth > MAX_DEPTH or len(raw) > MAX_BYTES:
        return ["archive-limit " + name]
    found = []
    is_zip = zipfile.is_zipfile(io.BytesIO(raw))
    is_tar = name.lower().endswith((".tar", ".tar.gz", ".tgz"))
    if not (is_zip or is_tar):
        if name.lower().endswith((".zip", ".whl")):
            return ["unreadable-archive " + name]
        return base._bytes(name, raw, markers)
    try:
        seen = set()
        if is_zip:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for info in archive.infolist():
                    member = name + "!/" + info.filename
                    budget[1] -= 1
                    budget[0] -= info.file_size
                    if min(budget) < 0:
                        return found + ["archive-limit " + name]
                    problem = base._archive_name(info.filename)
                    if info.filename in seen:
                        problem = "duplicate-member"
                    seen.add(info.filename)
                    if problem:
                        found.append(problem + " " + member)
                    elif stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                        found.append("special-file " + member)
                    elif not info.is_dir():
                        found.extend(inspect(archive.read(info), member, markers, depth=depth+1, budget=budget))
        else:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
                for info in archive:
                    member = name + "!/" + info.name
                    budget[1] -= 1
                    budget[0] -= info.size
                    if min(budget) < 0:
                        return found + ["archive-limit " + name]
                    problem = base._archive_name(info.name)
                    if info.name in seen:
                        problem = "duplicate-member"
                    seen.add(info.name)
                    if problem:
                        found.append(problem + " " + member)
                    elif info.isdir():
                        continue
                    elif not info.isfile():
                        found.append("special-file " + member)
                    else:
                        stream = archive.extractfile(info)
                        if stream is None:
                            raise ValueError("unreadable")
                        found.extend(inspect(stream.read(MAX_BYTES+1), member, markers, depth=depth+1, budget=budget))
    except (OSError, ValueError, RuntimeError, tarfile.TarError, zipfile.BadZipFile):
        found.append("unreadable-archive " + name)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path, action="append")
    parser.add_argument("--markers-file", type=Path)
    args = parser.parse_args(argv)
    try:
        markers = base.load_markers(args.markers_file, args.root)
        findings = []
        for archive in args.archive:
            if archive.stat().st_size > MAX_BYTES:
                findings.append("archive-limit " + archive.name)
            else:
                findings.extend(inspect(archive.read_bytes(), archive.name, markers))
    except (OSError, ValueError):
        print("SCAN_ERROR unreadable-input")
        return 1
    if findings:
        print("SCAN_FINDINGS " + str(len(findings)))
        print("\n".join(findings))
        return 1
    print("NESTED_SCAN_CLEAN " + ("external-markers-and-generic-rules" if markers else "generic-rules-only"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
