"""Application-level evidence checks, not an OS sandbox or remote attestation."""
from __future__ import annotations

from grokbot_mcp.registry import Workspace, policy_fingerprint
from grokbot_mcp.routine import routine_readiness
from grokbot_mcp.security.baseline import watched_paths, prefix_snapshot_options, capture_snapshot, load_snapshot, same_snapshot
from grokbot_mcp.storage import read_json
from grokbot_mcp.strict_json import LedgerTooLarge


def verify_evidence(workspace: Workspace, record: dict, assessment: dict,
                    *, require_claim: bool, unchanged: bool = False, registry=None,
                    review_head: bool = False) -> tuple[dict, dict]:
    """Check the frozen Manifest/proof/policy/baseline and actual claim bytes."""
    if assessment.get("ok") is not True:
        raise ValueError(str(assessment.get("error") or "validation_rejected"))
    if assessment.get("manifest_sha256") != record.get("manifest_sha256"):
        raise ValueError("hash_mismatch")
    if assessment.get("validation_sha256") != record.get("validation_result_sha256"):
        raise ValueError("validation_hash_mismatch")
    if assessment.get("validation_verified_at") != record.get("validation_verified_at"):
        raise ValueError("validation_decision_mismatch")
    if record.get("policy_fingerprint") != policy_fingerprint(workspace):
        raise ValueError("policy_mismatch")
    binding = routine_readiness(workspace, "execute", registry)
    if not binding["binding_confirmed"]:
        raise ValueError(str(binding["binding_error"]))
    if binding["binding_revision"] != record.get("binding_revision"):
        raise ValueError("binding_revision_mismatch")
    baseline = load_snapshot(workspace.root / ".bridge/execution_baselines",
                             str(record["execution_id"]), record.get("baseline_sha256"))
    document = assessment["document"]
    expected_baseline = (workspace.expected_head if workspace.git_gate == "strict"
                         else document.get("baseline_commit"))
    if baseline.get("git_head_text") != expected_baseline or document.get("baseline_commit") != expected_baseline:
        raise ValueError("git_head_mismatch")
    if require_claim:
        if record.get("execution_claim_state") != "claimed":
            raise ValueError("claim_required")
        path = workspace.root / ".bridge/execution_claims" / (str(record["execution_id"]) + ".json")
        try:
            claim, digest = read_json(path)
        except FileNotFoundError as exc:
            raise ValueError("claim_missing") from exc
        except LedgerTooLarge:
            raise
        except (OSError, ValueError) as exc:
            raise ValueError("claim_unreadable") from exc
        if not record.get("claim_sha256") or digest != record["claim_sha256"]:
            raise ValueError("claim_hash_mismatch")
        expected = {
            "schema_version": 2,
            "execution_id": record["execution_id"], "manifest_task_id": record["manifest_task_id"],
            "manifest_sha256": record["manifest_sha256"],
            "validation_result_sha256": record["validation_result_sha256"],
            "baseline_sha256": record["baseline_sha256"],
            "workspace_id": workspace.workspace_id, "machine": workspace.expected_machine,
            "policy_fingerprint": record["policy_fingerprint"], "binding_revision": record["binding_revision"],
            "allowed_files": document["allowed_files"], "claimed_at": record["claimed_at"],
            "target_check": "present", "enforcement": "advisory_precheck",
        }
        from grokbot_mcp.security.prefixes import active_prefix_fields
        expected.update(active_prefix_fields(workspace, document))
        creates = document.get("scope_expansion", {}).get("create_files")
        if creates is not None:
            expected["create_files"] = creates
            if creates:
                expected["target_check"] = "prospective"
        if workspace.git_gate == "advisory":
            expected.update(
                git_gate="advisory",
                baseline_commit=baseline.get("git_head_text"),
                existing_changes=sorted(set(baseline.get("diff_files") or [])
                                        | set(baseline.get("untracked") or [])),
            )
        if any(claim.get(key) != value for key, value in expected.items()):
            raise ValueError("claim_identity_mismatch")
    current = capture_snapshot(
        workspace.root,
        watched_paths(workspace, document),
        include_ignored=workspace.git_gate != "advisory",
        **prefix_snapshot_options(workspace, document),
    )
    if current.get("git_head_text") != baseline.get("git_head_text") and not (review_head and workspace.git_gate == "advisory"):
        raise ValueError("git_head_mismatch")
    if unchanged and workspace.git_gate == "strict" and not same_snapshot(baseline, current):
        raise ValueError("baseline_stale")
    return baseline, current


def creation_evidence(workspace, document: dict, baseline: dict, current: dict,
                      reported: list[str]) -> list[str] | None:
    """Measure optional creations and refuse agent-reported new out-of-task files.

    Concurrent unreported additions still use the advisory scope report. Historical
    tracked names come from Git, not a full tree persisted in the claim ledger.
    """
    import subprocess
    from grokbot_mcp.security.gitread import run_git
    from grokbot_mcp.security.paths import normalize_policy_path
    from grokbot_mcp.security.prefixes import prospective_file
    from grokbot_mcp.security.targets import TargetRejected

    creates = document.get("scope_expansion", {}).get("create_files")
    from grokbot_mcp.security.prefixes import manifest_workspace
    workspace = manifest_workspace(workspace, document)
    if not workspace.allowed_prefixes and workspace.prefix_ceiling is None:
        return None
    def key(name):
        return normalize_policy_path(name, casefold=workspace.git_gate == "advisory")

    allowed = {key(n) for n in document["allowed_files"]}
    outside = [n for n in reported if key(n) not in allowed]
    if outside:
        listing = run_git(workspace.root, ["ls-tree", "-r", "--name-only", "-z", baseline["git_head_text"]], subprocess.run)
        if listing.returncode:
            raise ValueError("git_status_failed")
        existing = {key(n) for n in listing.stdout.split("\0") if n}
        existing.update(key(n) for n in baseline.get("untracked", []))
        existing.update(key(n) for n, digest in baseline.get("file_hashes", {}).items() if digest is not None)
        if any(key(n) not in existing for n in outside):
            raise ValueError("scope_violation")
    if creates is None:
        return None
    missing = []
    for name in creates:
        try:
            prospective_file(workspace.root, name)
        except TargetRejected as exc:
            raise ValueError(exc.code) from exc
        if current.get("file_hashes", {}).get(name) is None:
            missing.append(name)
    return sorted(missing)
