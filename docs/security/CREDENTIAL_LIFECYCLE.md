# Credential lifecycle and controlled maintenance

Use the existing Windows Credential Manager and hidden-input wizard. The bridge has no remote key-rotation/revocation API and does not invent a provider button or expiration time. Only actual sending may load the selected credential value; a presence check wipes the native blob without decoding it. Diagnostic exports skip presence access unless separately requested.

Before maintenance, stop senders and explicitly switch submission off. Run `grokbot-safety credential-plan --registry <explicit> --workspace <id>`; unreadable ledgers, pending work and uncertain submissions are blockers. The command is read-only and never tests a key. `ok` means local maintenance prerequisites, not valid keys or submission readiness.

For an intended replacement, review the existing two Routines and their entry, workspace and capabilities in the provider's official UI. Use the provider-supported lifecycle mechanism; if none is available, keep that step unverified. Save new values only using the existing wizard's explicit `--replace-credentials` hidden-input flow. Never paste them in chat, command arguments, README, Issues or diagnostics. Recheck both bindings and the actual connected bridge identity afterwards. Keep the execution switch off until a separately authorized task.

If a credential is exposed, arrange explicit provider-side revocation/rotation first. Deleting a file or erasing history does not invalidate the old credential. Do not test a discovered credential by sending a task. Keep evidence private and do not delete unknown task records to make migration easier.

A 401/403, paused Routine, stale entry, unknown network outcome and explicit quota rejection are different conditions. The original HTTP classifier and no-resend rules remain. URL validation now rejects userinfo, fragments, private literal addresses and numeric aliases before opening a connection; it does not certify an arbitrary DNS host as a provider endpoint.

All automated lifecycle tests use in-memory credentials and synthetic endpoints; no real Credential Manager key has been exported, rotated or used by this candidate's CI.

## Fixture separation

Credential Manager targets are not scoped by registry-file location. Dev7 Canary
creation uses per-fixture random target namespaces and never probes existing
credentials. Old fixtures with shared target names remain unchanged and need
explicit operator review before any live use. A random target name is an identifier,
not a key, remote revocation, or permission to submit a task.
