"""Runtime identity. Path hashes are correlation identifiers, NOT anonymization."""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from pathlib import Path

from grokbot_mcp import __version__
from grokbot_mcp.registry import default_registry_path
from grokbot_mcp.storage import utc_now

CAPABILITIES = ("manifest-v1", "claim-once-v1", "claim-evidence-v2",
                "runtime-identity-v1", "result-contract-v1", "local-deployment-v1")
TEMPLATE_VERSION = "deployment-v1"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def path_id(kind: str, path: Path) -> str:
    # Keep the venv executable spelling: resolving its symlink can select base Python.
    return digest((kind + "\0" + os.path.normcase(str(path.absolute()))).encode("utf-8"))


def installation_id(python: Path, package: Path) -> str:
    return digest((path_id("python", python) + path_id("package", package)).encode("ascii"))


def package_digest(package: Path) -> str | None:
    try:
        # Do not follow linked package files into unrelated local data. This digest
        # identifies bytes, not trust or authenticity of the installed code.
        items = []
        for p in sorted(package.rglob("*")):
            stat = p.lstat()
            if p.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                return None
            if p.is_file() and p.suffix in {".py", ".md"}:
                if stat.st_nlink != 1:
                    return None
                items.append((str(p.relative_to(package)).replace("\\", "/"), digest(p.read_bytes())))
        return digest("\n".join(name + "\0" + value for name, value in items).encode("utf-8"))
    except OSError:
        return None


def registry_digest(path: Path) -> str | None:
    try:
        return digest(path.read_bytes())
    except OSError:
        return None


class RuntimeIdentity:
    def __init__(self, *, python: Path | None = None, package: Path | None = None,
                 registry: Path | None = None, version: str = __version__) -> None:
        self.python = python or Path(sys.executable)
        self.package = package or Path(__file__).parent
        self.registry = (registry or default_registry_path()).absolute()
        self.version = version
        self.instance_id = str(uuid.uuid4())
        self.initialized_at = utc_now()
        self.start_registry_sha256 = registry_digest(self.registry)
        self.start_package_sha256 = package_digest(self.package)

    def report(self, *, registry: Path | None = None, workspace_id: str | None = None,
               local_details: bool = False) -> dict:
        current = (registry or default_registry_path()).absolute()
        result = {
            "schema_version": 1, "bridge_version": self.version,
            "protocol_capabilities": list(CAPABILITIES), "template_version": TEMPLATE_VERSION,
            "installation_id": installation_id(self.python, self.package),
            "process_instance_id": self.instance_id, "process_id": os.getpid(),
            "identity_initialized_at": self.initialized_at,
            "package_sha256_at_start": self.start_package_sha256,
            "registry_id_at_start": path_id("registry", self.registry),
            "registry_sha256_at_start": self.start_registry_sha256,
            "registry_id": path_id("registry", current),
            "registry_sha256": registry_digest(current), "workspace_id": workspace_id,
            "identity_privacy": "correlation-identifiers-not-anonymized; redact-before-sharing",
            "snapshot_scope": "version/package/process/start-registry captured at identity initialization; current registry reread",
        }
        if local_details:
            result["local_paths"] = {"python": str(self.python.absolute()), "package": str(self.package.absolute()),
                                     "registry": str(current), "registry_at_start": str(self.registry)}
        return result


_RUNTIME = RuntimeIdentity()


def runtime_identity(workspace_id: str | None = None, *, registry: Path | None = None,
                     local_details: bool = False) -> dict:
    return _RUNTIME.report(registry=registry, workspace_id=workspace_id, local_details=local_details)


def compare_identity(expected: dict, observed: dict) -> dict:
    if type(expected) is not dict or type(observed) is not dict:
        return {"identity_matches": False, "missing_fields": ["runtime_identity"], "mismatched_fields": [],
                "client_reconnection_confirmed": False, "scope": "comparison only"}
    keys = ("bridge_version", "installation_id", "registry_id", "registry_sha256", "workspace_id")
    if "package_sha256_at_start" in expected:
        keys += ("package_sha256_at_start",)
    missing = [key for key in keys if expected.get(key) is None or observed.get(key) is None]
    mismatch = [key for key in keys if key not in missing and expected[key] != observed[key]]
    return {"identity_matches": not missing and not mismatch,
            "missing_fields": missing, "mismatched_fields": mismatch,
            "client_reconnection_confirmed": False,
            "scope": "comparison only; the origin of supplied client observations is not independently verified"}
