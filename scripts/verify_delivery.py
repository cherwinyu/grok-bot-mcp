"""Read-only delivery identity verification (standard library only).

Usage: python verify_delivery.py --bundle <delivery.zip> --sha256 <external zip hash>
Verifies member hashes, complete wheel RECORD, source Git tree and commit bytes.
This is NOT a signature, Windows test result, client reconnection or remote QA.
"""
from __future__ import annotations
import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import PurePosixPath, Path
import re
import stat
import sys
import zipfile


class IntegrityError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise IntegrityError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git_hash(kind, raw):
    return hashlib.sha1(kind.encode()+b" "+str(len(raw)).encode()+b"\0"+raw).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    return (bool(name) and not path.is_absolute() and ".." not in path.parts
            and "\\" not in name and ":" not in name and str(path) == name)


def members(archive):
    infos = archive.infolist()
    require(len(infos) <= 20000, "member_limit")
    names = [i.filename for i in infos]
    require(len(names) == len(set(names)), "duplicate_member")
    require(sum(i.file_size for i in infos) <= 128*1024*1024, "expanded_size_limit")
    for info in infos:
        require(safe_name(info.filename), "unsafe_member_path")
        require(not info.is_dir() and not stat.S_ISLNK(info.external_attr >> 16), "nonregular_member")
        require(not info.flag_bits & 1, "encrypted_member")
    return names


def wheel_record(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
        names = members(wheel)
        record = [n for n in names if n.endswith(".dist-info/RECORD")]
        require(len(record) == 1, "ambiguous_record")
        rows = list(csv.reader(io.StringIO(wheel.read(record[0]).decode("utf-8"))))
        require(all(len(row) == 3 for row in rows), "invalid_record_row")
        listed = [row[0] for row in rows]
        require(len(listed) == len(set(listed)) and set(listed) == set(names), "record_coverage_mismatch")
        checked = 0
        for name, digest, length in rows:
            data = wheel.read(name)
            if name == record[0]:
                require(digest == length == "", "record_self_digest")
                continue
            actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
            require(digest == "sha256="+actual and length == str(len(data)), "record_digest_mismatch")
            checked += 1
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        require(len(metadata) == 1, "ambiguous_metadata")
        match = re.search(r"^Version: (.+)$", wheel.read(metadata[0]).decode("utf-8"), re.M)
        require(match is not None, "missing_version")
        package = {n: wheel.read(n) for n in names if n.startswith("grokbot_mcp/")}
        require(bool(package), "package_missing")
        return match.group(1), checked, package


def source_tree(blobs, modes):
    require(type(blobs) is dict and type(modes) is dict and set(blobs) == set(modes), "source_index_mismatch")
    root = {}
    for name, oid in blobs.items():
        require(safe_name(name), "unsafe_source_path")
        require(type(oid) is str and re.fullmatch("[0-9a-f]{40}", oid), "invalid_blob_id")
        require(modes[name] in {"100644", "100755"}, "unsupported_source_mode")
        parts = name.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            require(type(node) is dict, "source_path_collision")
        require(parts[-1] not in node, "source_path_collision")
        node[parts[-1]] = (modes[name], oid)
    def tree(node):
        entries = []
        for name, value in node.items():
            directory = type(value) is dict
            mode, oid = ("40000", tree(value)) if directory else value
            body = mode.encode()+b" "+name.encode("utf-8")+b"\0"+bytes.fromhex(oid)
            entries.append(((name+("/" if directory else "")).encode("utf-8"), body))
        return git_hash("tree", b"".join(value for _, value in sorted(entries)))
    return tree(root)


def verify(bundle: Path, expected: str):
    require(bundle.stat().st_size <= 128*1024*1024, "bundle_size_limit")
    raw = bundle.read_bytes()
    require(sha(raw) == expected, "external_sha256_mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = members(archive)
        manifest = json.loads(archive.read("MANIFEST.json"))
        expected_files = manifest["files"]
        require(type(expected_files) is dict and set(names) == set(expected_files) | {"MANIFEST.json"}, "manifest_coverage_mismatch")
        for name, item in expected_files.items():
            data = archive.read(name)
            require(sha(data) == item["sha256"] and len(data) == item["size"], "member_digest_mismatch")
        provenance = json.loads(archive.read("provenance.json"))
        wheel_name = provenance["wheel_file"]
        version, count, package = wheel_record(archive.read(wheel_name))
        require(version == provenance["version"], "version_mismatch")
        require(sha(archive.read(wheel_name)) == provenance["wheel_sha256"], "wheel_provenance_mismatch")
        for name, data in package.items():
            source = "source/src/"+name
            require(source in expected_files and archive.read(source) == data, "wheel_source_mismatch")
        blobs = provenance["source_blobs"]
        require({"source/"+name for name in blobs} == {n for n in names if n.startswith("source/")}, "source_coverage_mismatch")
        for name, oid in blobs.items():
            require(git_hash("blob", archive.read("source/"+name)) == oid, "git_blob_mismatch")
        require(source_tree(blobs, provenance["source_modes"]) == provenance["source_tree"], "git_tree_mismatch")
        commit = provenance["source_commit_raw"].encode("utf-8")
        require(git_hash("commit", commit) == provenance["source_commit"], "git_commit_mismatch")
        require(commit.splitlines()[0] == ("tree "+provenance["source_tree"]).encode("ascii"), "commit_tree_mismatch")
        return {"integrity_verified": True, "source_commit": provenance["source_commit"],
                "source_tree": provenance["source_tree"], "version": version,
                "manifest_members": len(expected_files), "wheel_RECORD_members": count,
                "wheel_source_members": len(package), "windows_tests_rerun": False,
                "actual_client_reconnected": False, "remote_routine_verified": False, "qa_accepted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        result = verify(args.bundle, args.sha256.lower())
    except Exception as exc:
        code = str(exc) if type(exc) is IntegrityError else "invalid_or_unreadable_delivery"
        print(json.dumps({"integrity_verified": False, "code": code}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
