"""Bounded local observation; no transport, retries, or implicit QA approval."""
from __future__ import annotations

import json
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grokbot_mcp.storage import JsonLedger, read_json, utc_now


class LedgerView:
    """Evaluate against one record, optionally committing only actual transitions."""
    def __init__(self, store: JsonLedger, record: dict, persist: bool):
        self.store, self.record, self.persist = store, dict(record), persist
        self.updated = False

    def update(self, identity: str, **fields: object) -> dict:
        if any(self.record.get(key) != value for key, value in fields.items()):
            self.record.update(fields)
            if self.persist:
                self.store.update(identity, **fields)
                self.updated = True
        return dict(self.record)

    def read(self, identity: str) -> dict:
        return dict(self.record)


def observation_lock(store: JsonLedger, observe_only: bool):
    # A read-only query must not create directories or even a lock file.
    # Its result is a point-in-time observation, not a persisted acceptance.
    return nullcontext() if observe_only else store.locked()


def read_result(path: Path) -> tuple[dict | None, str | None, str]:
    """Retry ONLY incomplete JSON/UTF-8 or an atomic-replace gap, at most 4 reads.

    Identity/schema/security errors are not transient. Total sleep <= 150 ms;
    missing results are normal and never create a failed ledger entry.
    """
    for attempt in range(4):
        try:
            document, digest = read_json(path)
            return document, digest, "complete"
        except FileNotFoundError:
            return None, None, "missing"
        except (json.JSONDecodeError, UnicodeDecodeError):
            if attempt == 3:
                return None, None, "writing"
            time.sleep(0.05)
        except OSError as exc:
            raise ValueError("result_unreadable") from exc
    raise AssertionError("bounded_reader_unreachable")


def deadline(record: dict) -> dict:
    started = record.get("request_attempted_at")
    timeout = record.get("execution_timeout", 600)
    result = {"deadline_at": None, "deadline_exceeded": False}
    if not started:
        return result
    try:
        moment = datetime.fromisoformat(str(started))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("invalid_timeout")
        end = moment + timedelta(seconds=timeout)
        result.update(deadline_at=end.isoformat(), deadline_exceeded=datetime.now(timezone.utc) >= end)
    except (ValueError, OverflowError):
        result["deadline_error"] = "invalid_deadline"
    return result


def query_metadata(result: dict, view: LedgerView, observe_only: bool) -> dict:
    record = view.record
    result.update(
        last_observed_at=utc_now(), ledger_updated=view.updated,
        query_mode="observe_only" if observe_only else "reconcile",
        sent_this_call=False, attempted_this_call=False, webhook_sent=False,
        historical_submission_state=record.get("submission_state", record.get("task_state")),
        historical_webhook_attempted=record.get("webhook_attempted") is True,
        qa_accepted=False,
    )
    result.update(deadline(record))
    # Timeliness belongs to the original decision, not the date of a later query.
    result["late_result"] = record.get("late_result") is True
    if result.get("verification_scope") == "current":
        result["current_verified_at"] = result["last_observed_at"]
    else:
        result.setdefault("current_verified_at", None)
    result.setdefault("verified_at", record.get("verified_at"))
    result.setdefault("historical_status", record.get("historical_status"))
    result.setdefault("verification_scope", "none")
    result.setdefault("current_integrity", None)
    result.setdefault("integrity_error", None)
    return result
