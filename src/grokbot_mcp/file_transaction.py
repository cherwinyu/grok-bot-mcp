"""Recoverable local file transactions. Caller owns and derives every target path."""
from __future__ import annotations

from pathlib import Path

from grokbot_mcp.identity import digest
from grokbot_mcp.local_safety import LocalError, atomic_replace, create_new, current_hash, json_bytes, read_document, safe_path


def prepare(folder: Path, targets: dict[str, tuple[Path, bytes]], context: dict,
            *, attachments: dict[str, bytes] | None = None) -> dict:
    """Publish a COMPLETE recovery record before any target can be changed.

    A hard crash before directory rename leaves only private .preparing-* scratch;
    the requested transaction does not yet exist and originals remain unchanged.
    Retrying preparation uses a different scratch directory, never stale evidence.
    """
    import os
    import uuid
    safe_path(folder)
    if folder.exists():
        raise LocalError("transaction_exists")
    if set(targets) - {"client", "validate", "execute"}:
        raise LocalError("transaction_target_invalid")
    attachments = attachments or {}
    if set(attachments) - {"profile-snapshots.json", "routine-update.md"}:
        raise LocalError("transaction_target_invalid")
    folder.parent.mkdir(parents=True, exist_ok=True)
    scratch = folder.parent / ("." + folder.name + ".preparing-" + str(uuid.uuid4()))
    scratch.mkdir()
    receipt = {"schema_version": 1, "state": "prepared", "context": context, "files": {}, "attachments": {}}
    try:
        for key, (path, after) in targets.items():
            before_hash = current_hash(path)
            if before_hash is not None:
                before = path.read_bytes()
                if digest(before) != before_hash:
                    raise LocalError("input_changed")
                create_new(scratch / (key + ".before"), before)
            create_new(scratch / (key + ".after"), after)
            receipt["files"][key] = {"before": before_hash, "after": digest(after)}
        for name, raw in attachments.items():
            create_new(scratch / name, raw)
            receipt["attachments"][name] = digest(raw)
        create_new(scratch / "receipt.json", json_bytes(receipt))
        if folder.exists():
            raise LocalError("transaction_exists")
        os.rename(scratch, folder)
    except BaseException:
        # Only files created by this preparation; no user target or business evidence.
        for path in scratch.glob("*"):
            if path.is_file() and not path.is_symlink():
                path.unlink()
        scratch.rmdir()
        raise
    return receipt


def read_receipt(folder: Path, targets: dict[str, Path], context: dict | None = None) -> tuple[dict, str]:
    receipt, raw = read_document(folder / "receipt.json")
    if receipt.get("schema_version") != 1 or receipt.get("state") not in {"prepared", "applied", "rolled_back"}:
        raise LocalError("transaction_invalid")
    if set(receipt.get("files", {})) != set(targets) or (context is not None and receipt.get("context") != context):
        raise LocalError("transaction_context_mismatch")
    attachments = receipt.get("attachments", {})
    if type(attachments) is not dict or set(attachments) - {"profile-snapshots.json", "routine-update.md"}:
        raise LocalError("transaction_invalid")
    for name, expected in attachments.items():
        if type(expected) is not str or current_hash(folder / name) != expected:
            raise LocalError("transaction_backup_changed")
    for key, hashes in receipt["files"].items():
        if key not in {"client", "validate", "execute"} or type(hashes) is not dict:
            raise LocalError("transaction_invalid")
        for suffix in ("before", "after"):
            expected = hashes.get(suffix)
            path = folder / (key + "." + suffix)
            if expected is not None:
                if current_hash(path) != expected:
                    raise LocalError("transaction_backup_changed")
            elif path.exists():
                raise LocalError("transaction_backup_changed")
    return receipt, digest(raw)


def apply(folder: Path, targets: dict[str, Path], *, rollback: bool = False) -> dict:
    receipt, receipt_hash = read_receipt(folder, targets)
    destination = "before" if rollback else "after"
    # First check every target: a later conflict must not be discovered after a write.
    for key, path in targets.items():
        if current_hash(path) not in (receipt["files"][key]["before"], receipt["files"][key]["after"]):
            raise LocalError("transaction_target_changed")
    for key, path in targets.items():
        hashes = receipt["files"][key]
        current = current_hash(path)
        wanted = hashes[destination]
        if current == wanted:
            continue
        if wanted is None:
            safe_path(path)
            if current_hash(path) != current:
                raise LocalError("input_changed")
            path.unlink()  # Only a transaction-created local confirmation, never a ledger.
        else:
            atomic_replace(path, (folder / (key + "." + destination)).read_bytes(), expected=current)
    for key, path in targets.items():
        if current_hash(path) != receipt["files"][key][destination]:
            raise LocalError("transaction_readback_failed")
    receipt["state"] = "rolled_back" if rollback else "applied"
    atomic_replace(folder / "receipt.json", json_bytes(receipt), expected=receipt_hash)
    return receipt
