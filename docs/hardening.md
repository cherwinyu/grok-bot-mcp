# Path and robustness hardening

[简体中文](hardening.zh-CN.md) · [Security](security-model.md) · [Development](development.md)

For opt-in directory selection and audited recovery, see the [whole-repository advisory guide](advisory-workspaces.md). Its restricted glob grammar is deliberately narrower than general policy-path normalization.

Advisory registrations normalize allowed, forbidden and protected paths through one shared policy-path function. It converts backslashes, collapses repeated separators, leading `./` and internal `..`, and rejects absolute paths or traversal outside the root. Comparisons always use casefold in advisory mode, including Windows and Git repositories with `core.ignorecase=true`; `private2` is not a child of `private`. Actual spelling is retained for exact-file checks. Strict path handling and snapshot JSON serialization retain their existing behavior.

Advisory snapshots decode Git C-quoted paths, accumulating octal bytes before UTF-8 decoding. Invalid sequences remain unchanged in the decoder and are rejected for policy comparison. Ignored paths are kept only under watched paths. Execution forbidden-path checks use the same normalization; a reported `Private/secret.tmp` is a scope violation when `private` is forbidden. Result `changed_files` comparisons use Unicode NFC without guessing or repairing corrupted characters. This is still an application-level check, not a sandbox or attribution of concurrent writes.

Persisted JSON reads and writes enforce the existing 4 MiB byte limit. `ledger_too_large` identifies an oversized ledger, manifest, result, registry or snapshot; writes fail before replacing the target. The low-level `strict_json.decode` API retains its existing `json_too_large` code. Do not truncate ledgers, rewrite frozen evidence, create replacement identities or resend to work around the limit.

`grok_prepare_execution` continues to accept `workspace_id` and `manifest_task_id`. `grok_get_status` accepts `workspace_id`, `task_id`, `execution_id`, `observe_only` and `verify_current`. Unknown parameter names return `invalid_argument` with `accepted_arguments`. Unexpected tool/facade exceptions return `internal_error`, an exception type and a fixed safe reason; the existing local logging stream receives only the safe code and type. No stack, rejected values or exception message is copied. An internal error is not evidence that no send occurred; do not resend.

Text files and text subprocess output explicitly use UTF-8; subprocess decoding replaces invalid output bytes. JSON on disk remains UTF-8. Binary I/O retains binary mode. Run tests without setting `PYTHONUTF8`: `python -m pytest` uses `testpaths = ["tests"]`, keeping the standalone minimal example's tests intact. Installed-wheel verification also omits forced Python UTF-8 mode. Existing tests receive encoding arguments only; their assertions and expected outcomes are unchanged.

Verification requires native Windows tests, installed-wheel imports outside the source tree, fictional two-installation acceptance, the offline demo and release scans. An environment/permission failure is not a passed gate, and passing these checks does not authorize publication or deployment.

Directory authorization (`allowed_prefixes`) and exact `create_files` use a stricter literal path check before normalization, plus existing-component reparse and hardlink checks. Prefix snapshots use Git index/nonignored candidates, not ignored directory walks. Empty defaults retain legacy policy and result bytes; see [directory authorization](advisory-workspaces.md#directory-authorization).

## PR9 follow-up boundaries

Prefix-derived grants now exclude `.git`, `.bridge`, `.cursor` and `AGENTS.md`
as whole path segments, using shared policy normalization and case-insensitive
comparison. This applies to `create_files`, existing files selected through a
prefix and prefix-derived `allowed_globs` matches. Prefix-only snapshot discovery
also omits these candidates; explicit watched files and forbidden/protected
monitoring remain in place. Existing exact registry `allowed_files` exceptions
retain their previous behavior, including the existing forbidden checks. Registry
parsing and policy fingerprint inputs are unchanged. The earlier advice to configure
`.cursor` and `AGENTS.md` exclusions remains useful for exact-file policies and
monitoring; these names no longer require caller exclusions for derived grants.

Hardlink error codes intentionally remain distinct. Prefix snapshot capture
reports `reparse` for an existing file with multiple links, as it does for linked
or reparse components. Prospective `create_files` checks and exact existing-file
resolution report `target_hardlink_rejected` for a hardlinked leaf. Glob expansion
wraps unsafe-file checks as `allowed_glob_unsafe_file`. All refuse the operation;
the codes do not authorize repair of frozen evidence or resend.

`protected_or_forbidden_changed` can name a nested repository root containing
`.git`: the outer Git status may report an untracked repository directory or a
changed tracked gitlink as a single entry. The rejection is preserved. Its local
diagnostic gives conditional guidance, not an automatic classification of the
filesystem: an administrator should review excluding the nested directory from
the outer repository's changes or managing it separately, while retaining forbidden
protection. Ignore rules do not hide tracked gitlinks, and watched ignored paths
can still refuse. Do not remove forbidden policy merely to pass this check.
