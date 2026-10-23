"""Application-level evidence checks, not an OS sandbox or remote attestation."""
from __future__ import annotations

from grokbot_mcp.registry import Workspace, policy_fingerprint
from grokbot_mcp.routine import routine_readiness
from grokbot_mcp.security.baseline import capture_snapshot, load_snapshot, same_snapshot
from grokbot_mcp.storage import read_json


def verify_evidence(workspace: Workspace, record: dict, assessment: dict,
                    *, require_claim: bool, unchanged: bool = False, registry=None) -> tuple[dict, dict]:
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
        list(workspace.allowed_files) + list(workspace.forbidden_files),
        include_ignored=workspace.git_gate != "advisory",
    )
    if current.get("git_head_text") != baseline.get("git_head_text"):
        raise ValueError("git_head_mismatch")
    if unchanged and workspace.git_gate == "strict" and not same_snapshot(baseline, current):
        raise ValueError("baseline_stale")
    return baseline, current
