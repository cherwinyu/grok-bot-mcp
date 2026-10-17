# ADR 0001 — Windows-local execution, cloud trigger; no shared-disk assumption

Decision date: 2026-10-16. Status: engineering policy adopted; live-account acceptance NOT VERIFIED.

## Decision and basis

Keep the local Windows bridge, Credential Manager, registered Git workspace and seven tools. A cloud Routine may request separately approved commands on the selected Windows desktop. The profile explicitly names this topology; it cannot prove reachability or approval. If the desktop is unavailable or approval is refused, stop and preserve the original task. No unattended workaround, alternative cloud path or replacement send is permitted.

The protocol is `grok_bot_routine_bearer_v1`: the documented Routine POST uses Bearer authorization and 200 acceptance. The separate Grok Automation signing protocol is not autodetected or supported. No model selector, quota guarantee or generic Grok API gateway is added.

Sources checked on the decision date: [Cursor Routine documentation](https://cursor.com/help/grok-bot/routines) describes cloud triggers and acceptance semantics; [desktop settings](https://cursor.com/docs/grok-bot/settings) describes separate desktop execution permission and per-command default; [Grok Automation webhooks](https://docs.x.ai/grok/automations/webhooks) describes a different signed protocol. These references establish protocol descriptions, not certification of this project's real account flow or legal advice.

## Alternatives

A cloud-owned clone/result exchange would need a different remote authorization, artifact and claim design. It is deferred, not implemented by changing an absolute path. OS-enforced `isolated_write` would require a verified execution identity and filesystem boundary outside the model's control. It is rejected as unsupported today. Advisory mode is explicitly experimental for live use; per-command confirmation is recommended but cannot be enforced by declaring a string in a registry.

## Compatibility

An absent execution profile retains the existing policy hash and behavior and is marked legacy/unconfigured. An explicit profile has strict types, known protocol/topology/mode/approval fields, and enters the policy fingerprint. New wizard registrations include it. Existing bindings do not silently gain new authority; profile changes invalidate their matching policy. `read_only` blocks execution even if credentials and switches otherwise allow submission. Existing schema/version and proof/claim/terminal rules remain.

## Consequences and no-go

No real Routine was edited or invoked by this engineering work. Public experimental source and a supported stable execution product have different gates. Windows fictional CI cannot close actual desktop/Routine acceptance. Multiple Bots or prompts are not an OS isolation boundary. Do not relax provider approval to make an absent desktop appear usable.
