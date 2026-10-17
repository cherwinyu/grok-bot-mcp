"""Synthetic tampering tests for the reviewed Beta sdist source identity boundary."""
from __future__ import annotations

import io
import tarfile
import unittest

from scripts.verify_beta_assets import CandidateError, SDIST_GENERATED, sdist_members


VERSION = "0.1.4b1"
SOURCES = {"src/grokbot_mcp/__init__.py": b"__version__ = '0.1.4b1'\n",
           "README.md": b"fictional documentation\n"}


def fake_sdist(*, changed=False, missing=False, extra=False, duplicate=False, special=False):
    items = {**SOURCES, **{name: b"fictional generated metadata\n" for name in SDIST_GENERATED}}
    if changed:
        items["src/grokbot_mcp/__init__.py"] = b"incorrect source bytes"
    if missing:
        items.pop("README.md")
    if extra:
        items["src/grokbot_mcp/unreviewed.py"] = b"unapproved fixture"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        root = tarfile.TarInfo(f"grokbot_mcp_bridge-{VERSION}")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for name in sorted(items):
            data = items[name]
            meta = tarfile.TarInfo(f"grokbot_mcp_bridge-{VERSION}/" + name)
            meta.size = len(data)
            archive.addfile(meta, io.BytesIO(data))
        if duplicate:
            meta = tarfile.TarInfo(f"grokbot_mcp_bridge-{VERSION}/README.md")
            meta.size = 1
            archive.addfile(meta, io.BytesIO(b"a"))
        if special:
            meta = tarfile.TarInfo(f"grokbot_mcp_bridge-{VERSION}/link")
            meta.type = tarfile.SYMTYPE
            meta.linkname = "../private"
            archive.addfile(meta)
    return out.getvalue()


class SdistSourceIdentityTest(unittest.TestCase):
    def test_exact_source_identity_passes(self):
        self.assertEqual(sdist_members(fake_sdist(), SOURCES, VERSION), len(SOURCES))

    def test_modified_source_is_refused(self):
        with self.assertRaisesRegex(CandidateError, "sdist_source_content_mismatch"):
            sdist_members(fake_sdist(changed=True), SOURCES, VERSION)

    def test_missing_and_extra_source_refused(self):
        for flag in ("missing", "extra"):
            with self.subTest(flag=flag), self.assertRaisesRegex(
                    CandidateError, "sdist_source_file_set_mismatch"):
                sdist_members(fake_sdist(**{flag: True}), SOURCES, VERSION)

    def test_duplicate_member_is_refused(self):
        with self.assertRaisesRegex(CandidateError, "sdist_duplicate_member"):
            sdist_members(fake_sdist(duplicate=True), SOURCES, VERSION)

    def test_special_link_is_refused(self):
        with self.assertRaisesRegex(CandidateError, "sdist_special_member"):
            sdist_members(fake_sdist(special=True), SOURCES, VERSION)
