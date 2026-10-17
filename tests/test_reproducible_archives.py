"""Offline timestamp and metadata controls for source ZIP, wheel and sdist."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import tarfile
import unittest
import zipfile

from scripts.reproducible_archives import (
    UNIX_EPOCH, ZIP_EPOCH, canonical_sdist, canonical_zip,
)
from scripts.verify_beta_assets import record_members


def zip_sample(date, *, extra=b"", duplicate=False):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        entry = zipfile.ZipInfo("src/file.py", date_time=date)
        entry.extra = extra
        entry.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(entry, b"identical python file\n")
        if duplicate:
            archive.writestr(entry, b"second member")
    return out.getvalue()


def tar_sample(timestamp, *, add_link=False, add_pax=False):
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", filename="different-build-name",
                       mtime=timestamp) as gz:
        with tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            entry = tarfile.TarInfo("package-0.1.4b1/src/file.py")
            entry.size = len(b"identical python file\n")
            entry.mtime = timestamp + 0.75
            if add_pax:
                entry.pax_headers = {"comment": "unsupported"}
            archive.addfile(entry, io.BytesIO(b"identical python file\n"))
            if add_link:
                link = tarfile.TarInfo("package-0.1.4b1/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "../private"
                archive.addfile(link)
    return out.getvalue()


class ReproducibleArchiveTests(unittest.TestCase):
    def test_same_zip_source_bytes_different_clock_produce_identical_archive(self):
        first = canonical_zip(zip_sample((2026, 10, 17, 10, 2, 2)))
        second = canonical_zip(zip_sample((2026, 10, 17, 17, 5, 24)))
        self.assertEqual(first, second)
        self.assertEqual(canonical_zip(first), first)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(archive.infolist()[0].date_time, ZIP_EPOCH)
            self.assertEqual(archive.read("src/file.py"), b"identical python file\n")

    def test_same_sdist_source_bytes_different_clock_produce_identical_archive(self):
        first = canonical_sdist(tar_sample(1000000000))
        second = canonical_sdist(tar_sample(1700000000))
        self.assertEqual(first, second)
        self.assertEqual(canonical_sdist(first), first)
        self.assertEqual(first[3] & 0x08, 0)
        self.assertEqual(first[4:8], b"\0\0\0\0")
        with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
            entry = archive.getmembers()[0]
            self.assertEqual(entry.mtime, UNIX_EPOCH)
            self.assertEqual(entry.pax_headers, {})
            self.assertEqual(archive.extractfile(entry).read(), b"identical python file\n")

    def test_reject_unknown_zip_extra_or_duplicate_member(self):
        for item in (zip_sample((2026, 10, 17, 0, 0, 0), extra=b"\x55\x54\x00\x00"),
                     zip_sample((2026, 10, 17, 0, 0, 0), duplicate=True)):
            with self.subTest(size=len(item)), self.assertRaises(ValueError):
                canonical_zip(item)

    def test_reject_sdist_symlink_or_unknown_pax_metadata(self):
        for option in ({"add_link": True}, {"add_pax": True}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                canonical_sdist(tar_sample(1700000000, **option))

    def test_wheel_record_payload_remains_valid_after_rewrite(self):
        content = b"package test\n"
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode("ascii").rstrip("=")
        record = (f"grokbot_mcp/__init__.py,sha256={digest},{len(content)}\n"
                  "grokbot_mcp_bridge-0.1.4b1.dist-info/RECORD,,\n")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("grokbot_mcp/__init__.py", content)
            archive.writestr("grokbot_mcp_bridge-0.1.4b1.dist-info/RECORD", record)
        with zipfile.ZipFile(io.BytesIO(canonical_zip(out.getvalue()))) as archive:
            self.assertEqual(record_members(archive), 2)


if __name__ == "__main__":
    unittest.main()
