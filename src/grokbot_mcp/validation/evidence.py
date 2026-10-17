"""Require a durable validation decision, not just a replaceable success file."""
from __future__ import annotations

from datetime import datetime, timezone

from grokbot_mcp.registry import Workspace
from grokbot_mcp.storage import read_json


def verify_validation_decision(workspace: Workspace, task_id: str,
                               manifest_sha256: str, proof_sha256: str,
                               proof: dict) -> str:
    """Return the recorded verification time or fail closed without reconciling.

    Ledger records are atomically replaced. Read a snapshot without taking a
    writing lock so observe_only remains read-only. A task's terminal decision
    and result bytes must agree; a read-only observation cannot grant authority.
    This is application-level consistency, not protection against an actor able
    to replace the ledger and every other evidence file together.
    """
    path = workspace.root / '.bridge/tasks_v2' / (task_id + '.json')
    try:
        record, _ = read_json(path)
    except FileNotFoundError as exc:
        raise ValueError('validation_ledger_missing') from exc
    except (OSError, ValueError) as exc:
        raise ValueError('validation_ledger_unreadable') from exc
    if (record.get('task_id') != task_id or record.get('action') != 'validate_manifest'
            or record.get('manifest_sha256') != manifest_sha256
            or record.get('result_path') != '.bridge/results_v2/' + task_id + '.json'):
        raise ValueError('validation_ledger_identity_mismatch')
    if record.get('terminal') is not True:
        raise ValueError('validation_not_verified')
    if (record.get('task_state') != 'succeeded' or record.get('historical_status') != 'succeeded'
            or record.get('submission_state') != 'accepted' or record.get('accepted') is not True
            or record.get('error') is not None):
        raise ValueError('validation_not_succeeded')
    if record.get('result_sha256') != proof_sha256:
        raise ValueError('validation_hash_mismatch')
    summary = record.get('verified_summary')
    keys = ('validation_passed', 'validation_errors', 'code_modified',
            'manifest_sha256', 'workspace_id', 'allowed_files', 'error')
    if (not isinstance(summary, dict) or any(key not in summary for key in keys)
            or any(summary.get(key) != proof.get(key) for key in keys)
            or summary.get('validation_passed') is not True
            or summary.get('code_modified') is not False
            or summary.get('error') is not None
            or not isinstance(record.get('verified_snapshot'), dict)):
        raise ValueError('validation_decision_mismatch')
    verified_at = record.get('verified_at')
    try:
        if not isinstance(verified_at, str):
            raise ValueError('missing_time')
        moment = datetime.fromisoformat(verified_at)
        if moment.tzinfo is None or moment > datetime.now(timezone.utc):
            raise ValueError('invalid_time')
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('validation_not_verified') from exc
    return verified_at
