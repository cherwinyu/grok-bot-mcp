"""Read-only bounded status polling with an injectable monotonic clock."""
from __future__ import annotations

import time

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timeout", "identity_mismatch",
                      "hash_mismatch", "scope_violation", "unknown_submission", "result_inconsistent",
                      "external_agent_rejected", "rejected", "archived"})


def wait_for_status(probe, seconds, *, clock=None, sleep=None):
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    start = clock()
    delay = 1
    while True:
        results = probe()
        if any(item.get("status") in TERMINAL or (
                item.get("result_observation") == "complete" and item.get("error") is None
                and item.get("result_diagnosis", {}).get("category") not in {"format_error", "identity_error"})
               for item in results):
            break
        remaining = seconds - (clock() - start)
        if remaining <= 0 or not results:
            break
        sleep(min(delay, remaining))
        delay = min(delay * 2, 5)
    return max(0, round((clock() - start) * 1000))
