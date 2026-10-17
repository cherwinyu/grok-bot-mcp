"""Synthetic negative tests for byte-identical Beta artifact checks."""
from __future__ import annotations

import base64
import hashlib
import io
import zipfile
import unittest

from scripts.verify_beta_assets import CandidateError, checksums, record_members


def package(*, tamper=False, omit=False) -> bytes:
    raw = b"version = 1"
    digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    line = "grokbot_mcp/__init__.py,sha256=" + digest + "," + str(len(raw)) + "\n"
    if tamper:
        line = line.replace("sha256=" + digest, "sha256=" + "A" * len(digest))
    if omit:
        line = ""
    line += "grokbot_mcp_bridge-0.1.4b1.dist-info/RECORD,,\n"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("grokbot_mcp/__init__.py", raw)
        archive.writestr("grokbot_mcp_bridge-0.1.4b1.dist-info/RECORD", line)
    return stream.getvalue()


class CandidateArtifactContractTest(unittest.TestCase):
    def test_strict_checksum_manifest(self):
        raw = ("a" * 64 + "  candidate.whl\n").encode()
        self.assertEqual(checksums(raw), {"candidate.whl": "a" * 64})
        for broken in (b"", raw + raw, raw.rstrip(b"\n"), b"garbage\n",
                       ("A" * 64 + "  candidate.whl\n").encode()):
            with self.subTest(value=broken[:8]), self.assertRaises(CandidateError):
                checksums(broken)

    def test_rejects_checksum_path_or_extra_fields(self):
        for name in ("../private.whl", "folder/name.whl", "candidate.whl  unknown"):
            with self.subTest(name=name), self.assertRaises(CandidateError):
                checksums(("a" * 64 + "  " + name + "\n").encode())

    def test_valid_record_proves_all_wheel_members(self):
        with zipfile.ZipFile(io.BytesIO(package())) as archive:
            self.assertEqual(record_members(archive), 2)

    def test_tamper_or_missing_record_row_refused(self):
        for damaged in (package(tamper=True), package(omit=True)):
            with zipfile.ZipFile(io.BytesIO(damaged)) as archive, self.assertRaises(CandidateError):
                record_members(archive)

    def test_duplicate_zip_names_refused(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("grokbot_mcp/__init__.py", b"a")
            archive.writestr("grokbot_mcp/__init__.py", b"b")
            archive.writestr("grokbot_mcp_bridge-0.1.4b1.dist-info/RECORD",
                             "grokbot_mcp_bridge-0.1.4b1.dist-info/RECORD,,\n")
        with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive, self.assertRaises(CandidateError):
            record_members(archive)
