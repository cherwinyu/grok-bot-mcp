"""Local operator cancellation of provably unsubmitted authorization only.

Not exposed as an MCP tool. Does not grant authority, delete evidence, restore an
attempt, change a binding, send a Webhook, or attest OS administrator identity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from grokbot_mcp.registry import RegistryError, default_registry_path, load_registry
from grokbot_mcp.security.paths import is_reparse_point, is_uuid
from grokbot_mcp.storage import JsonLedger, utc_now

REASONS = ('binding_repair', 'operator_cancelled')


def cancellation_blocker(workspace, execution_id: str, record: dict) -> str | None:
    """The original cancellation predicates, shared by preview and locked mutation."""
    bridge = workspace.root / '.bridge'
    # Missing or contradictory fields do not establish an unused attempt.
    required = {'execution_state': 'prepared', 'submission_state': 'not_sent',
                'execution_claim_state': 'unclaimed', 'request_attempted_at': None,
                'claimed_at': None, 'webhook_attempted': False, 'submission_final': False,
                'http_status': None, 'accepted': None}
    if (any(key not in record or type(record[key]) is not type(value) or record[key] != value
            for key, value in required.items())
            or record.get('approval_state') not in {'granted', 'revoked'}
            or record.get('terminal') or record.get('claim_sha256') or record.get('result_sha256')):
        return 'cancellation_requires_unsubmitted_authorization'
    for kind in ('execution_claims', 'results_execute'):
        path = bridge / kind / (execution_id + '.json')
        if (path.exists() or path.is_symlink()
                or any(is_reparse_point(p) for p in (path, *path.parents))):
            return 'cancellation_evidence_exists'
    return None


def preview_cancellation(workspace, execution_id: str) -> dict:
    """Read-only eligibility snapshot, NOT authority to cancel; creates no lock."""
    try:
        if not is_uuid(execution_id):
            return {'eligible': False, 'error': 'invalid_execution_id'}
        store = JsonLedger(workspace.root / '.bridge' / 'executions')
        record = store.read(execution_id)
        if record.get('execution_id') != execution_id:
            return {'eligible': False, 'error': 'execution_identity_mismatch'}
        if record.get('execution_state') == 'cancelled':
            return {'eligible': False, 'error': 'authorization_cancelled'}
        problem = cancellation_blocker(workspace, execution_id, record)
        return {'eligible': problem is None, 'error': problem}
    except (OSError, ValueError):
        return {'eligible': False, 'error': 'missing_or_unreadable_execution'}


def cancel_unsubmitted(workspace, execution_id: str, *, reason: str) -> dict:
    if not is_uuid(execution_id) or reason not in REASONS:
        return {'ok': False, 'error': 'invalid_cancellation'}
    bridge = workspace.root / '.bridge'
    store = JsonLedger(bridge / 'executions')
    if any(is_reparse_point(p) for p in (store.directory, *store.directory.parents)):
        return {'ok': False, 'error': 'reparse'}
    with store.locked():
        try:
            record = store.read(execution_id)
        except (OSError, ValueError):
            return {'ok': False, 'error': 'missing_or_unreadable_execution'}
        if record.get('execution_id') != execution_id:
            return {'ok': False, 'error': 'execution_identity_mismatch'}
        if record.get('execution_state') == 'cancelled':
            return {'ok': True, 'execution_id': execution_id, 'already_cancelled': True,
                    'cancelled_at': record.get('cancelled_at'), 'webhook_sent': False}
        problem = cancellation_blocker(workspace, execution_id, record)
        if problem:
            return {'ok': False, 'error': problem}
        now = utc_now()
        store.update(execution_id, approval_state='revoked', execution_state='cancelled',
                     terminal=True, historical_status='cancelled', cancelled_at=now,
                     cancellation_reason=reason, cancellation_source='local_operator_cli',
                     error='authorization_cancelled', qa_state='not_reviewed')
        return {'ok': True, 'execution_id': execution_id, 'already_cancelled': False,
                'cancelled_at': now, 'webhook_sent': False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Cancel a never-submitted authorization; preserve every evidence file.')
    parser.add_argument('--registry', type=Path, default=default_registry_path())
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--execution-id', required=True)
    parser.add_argument('--reason', choices=REASONS, required=True)
    parser.add_argument('--confirm', action='store_true', help='Explicit local operator confirmation is required.')
    args = parser.parse_args(argv)
    if not args.confirm:
        result = {'ok': False, 'error': 'cancellation_confirmation_required'}
    else:
        try:
            workspace = load_registry(args.registry)[args.workspace]
            result = cancel_unsubmitted(workspace, args.execution_id, reason=args.reason)
        except (RegistryError, KeyError, OSError, ValueError):
            result = {'ok': False, 'error': 'cancellation_failed_closed'}
    print(json.dumps({**result, 'webhook_sent': False, 'qa_accepted': False}, ensure_ascii=True))
    return 0 if result.get('ok') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
