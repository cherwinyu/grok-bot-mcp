"""Release privacy checks use fictional markers and temporary archives only."""
from __future__ import annotations

import contextlib
import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import build_release, scan_release


class ReleaseTest(unittest.TestCase):
    def test_external_markers_detect_split_and_escaped_python_strings(self) -> None:
        marker = "fictional-private-identity"
        source = 'value = "fictional-private-" + "identity"\n'
        escaped = 'value = "' + ''.join('\\x' + format(ord(c), '02x') for c in marker) + '"\n'
        for text in (source, escaped):
            self.assertEqual(scan_release._inspect("example.py", text, (marker,)), ["private-marker example.py"])

    def test_private_marker_file_is_external_and_output_never_echoes_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root = directory / "public"
            root.mkdir()
            marker = "fictional-private-identity"
            markers = directory / "markers.json"
            markers.write_text(json.dumps([marker]), encoding="utf-8")
            (root / "sample.txt").write_text(marker, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = scan_release.main(["--root", str(root), "--markers-file", str(markers)])
            self.assertEqual(result, 1)
            self.assertNotIn(marker, output.getvalue())
            with self.assertRaisesRegex(ValueError, "outside_public_root"):
                scan_release.load_markers(root / "markers.json", root)
            markers.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "marker_file_invalid"):
                scan_release.load_markers(markers, root)

    def test_zip_wheel_and_sdist_contents_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = "fictional-private-identity"
            for suffix in (".zip", ".whl", ".tar.gz"):
                path = Path(tmp) / ("release" + suffix)
                if suffix == ".tar.gz":
                    with tarfile.open(path, "w:gz") as archive:
                        raw = marker.encode()
                        info = tarfile.TarInfo("package/readme.txt")
                        info.size = len(raw)
                        archive.addfile(info, io.BytesIO(raw))
                else:
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("package/readme.txt", marker)
                self.assertEqual(scan_release._scan_archive(path, (marker,)), ["private-marker package/readme.txt"])

    def test_private_paths_are_rejected_even_without_private_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "OpenSource" / "_private_release_audit").mkdir(parents=True)
            self.assertIn("private-path OpenSource", scan_release._scan_tree(root))
            archive_path = root / "candidate.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("package/_private_release_audit/report.txt", "local only")
                archive.writestr("package/private-markers.json", "[]")
                archive.writestr("../outside.txt", "bad path")
                archive.writestr("package/.git/config", "private history")
            found = scan_release._scan_archive(archive_path)
            self.assertEqual(len(found), 4)
            self.assertTrue(any(s.startswith("unsafe-path") for s in found))
            self.assertTrue(any(s.startswith("packaged-cache") for s in found))

    def test_public_file_list_excludes_private_and_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in build_release.ROOT_FILES:
                (root / name).write_text("public", encoding="utf-8")
            for name in ("src/example.py", "src/__pycache__/example.pyc", "OpenSource/_private_release_audit/report.md", ".git/config", ".venv/local.txt", "private-markers.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            names = {p.relative_to(root).as_posix() for p in build_release.public_files(root)}
            self.assertEqual(names, set(build_release.ROOT_FILES) | {"src/example.py"})

    def test_secret_and_personal_path_rules_remain_active(self) -> None:
        secret = "sk_" + ("a" * 24)
        personal_path = "/".join(["C:", "Users", "fictional-user", "file.txt"])
        self.assertEqual(scan_release._inspect("sample.txt", secret), ["secret-shape sample.txt"])
        self.assertEqual(scan_release._inspect("sample.txt", personal_path), ["user-path sample.txt"])

    def test_unreadable_archive_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.tar.gz"
            path.write_bytes(b"not an archive")
            self.assertEqual(scan_release._scan_archive(path), ["unreadable-archive broken.tar.gz"])
