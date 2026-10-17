"""Windows Credential Manager backend.

The target name is chosen by the workspace administrator. This module never
contains a URL or key, and it never enumerates unrelated credentials.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from grokbot_mcp.credentials.blob import BlobError, unpack_blob

CRED_TYPE_GENERIC = 1
ERROR_NOT_FOUND = 1168
CRED_PERSIST_LOCAL_MACHINE = 2


class CredentialError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FILETIME(ctypes.Structure):
    _fields_ = (("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD))


class CREDENTIALW(ctypes.Structure):
    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    )


def _apis() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    kernel32.RtlZeroMemory.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    kernel32.RtlZeroMemory.restype = None
    return advapi32, kernel32


def assert_distinct(target: str, others: tuple[str, ...]) -> None:
    if not target or target in others or any(target == item for item in others):
        raise CredentialError("target_rejected")


def credential_status(target: str, *, others: tuple[str, ...] = ()) -> str:
    """Return present, missing, or unreadable. The blob is wiped and never decoded."""
    try:
        assert_distinct(target, others)
        advapi32, kernel32 = _apis()
        slot = ctypes.POINTER(CREDENTIALW)()
        ok = advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(slot))
        if not ok:
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return "missing"
            return "unreadable"
        try:
            cred = slot.contents
            if cred.CredentialBlob and cred.CredentialBlobSize:
                kernel32.RtlZeroMemory(cred.CredentialBlob, cred.CredentialBlobSize)
        finally:
            advapi32.CredFree(slot)
        return "present"
    except CredentialError:
        return "unreadable"
    except OSError:
        return "unreadable"


def load_credential(target: str, *, others: tuple[str, ...] = ()) -> tuple[str, str]:
    """Read one named target. Callers must not log the returned pair."""
    assert_distinct(target, others)
    advapi32, kernel32 = _apis()
    slot = ctypes.POINTER(CREDENTIALW)()
    ok = advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(slot))
    if not ok:
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            raise CredentialError("missing")
        raise CredentialError("unreadable")
    raw = b""
    try:
        cred = slot.contents
        size = int(cred.CredentialBlobSize)
        if cred.CredentialBlob and size:
            raw = ctypes.string_at(cred.CredentialBlob, size)
            kernel32.RtlZeroMemory(cred.CredentialBlob, size)
    finally:
        advapi32.CredFree(slot)
    try:
        return unpack_blob(raw)
    except BlobError as exc:
        raise CredentialError("unreadable") from exc
    finally:
        raw = b""


def store_credential(target: str, blob: bytes) -> None:
    """Write one generic credential. The blob is not printed."""
    if not target or not blob:
        raise CredentialError("target_rejected")
    advapi32, kernel32 = _apis()
    buffer = ctypes.create_string_buffer(blob)
    cred = CREDENTIALW()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.c_void_p)
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = "webhook"
    cred.Comment = "grokbot-mcp webhook"
    ok = advapi32.CredWriteW(ctypes.byref(cred), 0)
    kernel32.RtlZeroMemory(ctypes.cast(buffer, ctypes.c_void_p), len(blob))
    if not ok:
        raise CredentialError("unreadable")
