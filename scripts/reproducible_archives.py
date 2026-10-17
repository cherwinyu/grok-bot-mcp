"""Canonicalize archive timestamps without changing any packaged member bytes.

The archive metadata epoch is deliberately constant across PR and squash-merged
commits. It is **not** a source provenance timestamp or a publication approval.
Built archives are verified and scanned independently after canonicalization.
"""
from __future__ import annotations

import copy
import gzip
import io
from pathlib import Path
import stat
import tarfile
import zipfile

# ZIP's earliest supported date. Unlike git commit dates, this value is stable
# when the same reviewed tree is rebuilt from a different commit (squash merge).
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
UNIX_EPOCH = 315532800
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def _bounded(raw: bytes) -> None:
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("release_archive_too_large")


def canonical_zip(raw: bytes) -> bytes:
    """Preserve member bytes, order, compression type, permissions, and RECORD.

    Only the ZIP entry timestamp is normalized. Unknown extended metadata is
    refused instead of silently deleting signatures or provenance information.
    """
    _bounded(raw)
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as source, zipfile.ZipFile(
            result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        if source.comment:
            raise ValueError("release_zip_comment_unreviewed")
        members = source.infolist()
        if len({item.filename for item in members}) != len(members):
            raise ValueError("release_zip_duplicate_member")
        for original in members:
            if original.is_dir() or original.flag_bits & 1 or original.extra or original.comment:
                raise ValueError("release_zip_extra_metadata_unreviewed")
            if stat.S_ISLNK(original.external_attr >> 16):
                raise ValueError("release_zip_link_member")
            info = copy.copy(original)
            info.date_time = ZIP_EPOCH
            output.writestr(info, source.read(original),
                            compress_type=info.compress_type, compresslevel=9)
    return result.getvalue()


def canonical_sdist(raw: bytes) -> bytes:
    """Preserve the complete sdist payload and member metadata except mtime.

    Strip gzip's original filename and wall-clock time. Set tar mtime to a
    constant, including the PAX extended mtime (which is otherwise volatile).
    Unreviewed PAX metadata and special file types remain release blockers.
    """
    _bounded(raw)
    result = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
        with gzip.GzipFile(fileobj=result, mode="wb", filename="", mtime=0,
                           compresslevel=9) as zipped:
            with tarfile.open(fileobj=zipped, mode="w|", format=tarfile.PAX_FORMAT) as output:
                seen: set[str] = set()
                for original in source:
                    if original.name in seen:
                        raise ValueError("release_sdist_duplicate_member")
                    seen.add(original.name)
                    if not original.isfile() and not original.isdir():
                        raise ValueError("release_sdist_special_member")
                    if set(original.pax_headers) - {"mtime"}:
                        raise ValueError("release_sdist_pax_unreviewed")
                    info = copy.copy(original)
                    info.mtime = UNIX_EPOCH
                    info.pax_headers = {}
                    stream = source.extractfile(original) if original.isfile() else None
                    if original.isfile() and stream is None:
                        raise ValueError("release_sdist_member_unreadable")
                    output.addfile(info, stream)
    return result.getvalue()


def canonicalize_release(directory: Path) -> None:
    """Rewrite only the three expected build outputs in a staging directory."""
    files = sorted(path for path in directory.iterdir() if path.is_file())
    if len(files) != 3 or sum(path.name.endswith(".whl") for path in files) != 1 or sum(
            path.name.endswith("-source.zip") for path in files) != 1 or sum(
            path.name.endswith(".tar.gz") for path in files) != 1:
        raise ValueError("unexpected_reproducible_release_file_set")
    for path in files:
        source = path.read_bytes()
        transformed = canonical_sdist(source) if path.name.endswith(".tar.gz") else canonical_zip(source)
        path.write_bytes(transformed)
