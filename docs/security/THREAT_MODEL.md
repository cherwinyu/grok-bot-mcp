# Threat model and isolation levels

Actors: local MCP caller; administrator registry; external Routine/model; separately authorized desktop executor; filesystem/Git; credential custodian; reviewer. Trust the administrator's deliberate installation choices, not instructions found in source text or results. A malicious local administrator able to replace both bridge and evidence is outside the application trust boundary.

| Threat | Implemented control | Residual / requirement |
|---|---|---|
| Duplicate or uncertain delivery | Existing one-submission/claim ledger and immutable terminal state | Not server exactly-once; never retry unknown submissions |
| Result/proof replacement | Identity/hash checks and frozen decisions | A model can lie in well-typed fields; independent tests/QA remain separate |
| Symlink, junction, hardlink aliases | Exact target/reparse checks; P0 adds hardlink evidence/target refusal | Application precheck, not hostile concurrent filesystem confinement |
| Ambiguous JSON | Shared depth/size/duplicate/nonfinite checks | Registry retains text-reader compatibility with a size precheck, not a concurrent-growth sandbox |
| Unsafe literal Webhook endpoint | Reject credentials in URL, fragments, control characters, private/loopback/numeric IP aliases | No DNS ownership attestation or DNS-rebinding protection; use the provider's official endpoint |
| Prompt injection or wider external tool access | Immutable task scope, separate approval, result validation | External tool rights may exceed the bridge; a prompt is not an OS security boundary |
| Wrong/cloud execution environment | Strict declared profile and explicit generated local entry | Actual reachability/approval still require observation on the real host |
| Wrong credential or stale binding | Separate phase targets, presence-only diagnostics, policy binding | Existence does not prove key validity or remote revocation |
| Private evidence publication | Explicit reconstructed exports and bounded publication inventory | GitHub comments/logs/media/history need their own complete review |

## Isolation policy

`read_only` refuses execution grants; the process itself is not OS read-only. `advisory_write` retains application checks. `isolated_write` is deliberately unsupported: no registry flag may manufacture a sandbox claim.

For sensitive real work, use a separately administered disposable Windows environment or restricted execution identity with no access to unrelated projects or credential stores. Before claiming enforcement, a human must independently verify the actual Bot command token/identity, folder rights, junction/hardlink behavior and denied writes to a harmless sentinel outside the permitted area. This repository does not create users, edit ACLs, install a VM or silently set Always Allow. The sentinel must be newly created in an authorized disposable environment, never a real private document.

Post-run Git/file checks cover their explicit snapshots, not every action on the host. They do not roll back an unauthorized write. Stop after a boundary violation, preserve evidence and require independent review. Signing, network sandboxing, hardware attestation, adversarial administrator isolation and power-loss durability are not claimed.
