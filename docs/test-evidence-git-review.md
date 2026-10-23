# Optional test policy, output evidence and Git review

[简体中文](test-evidence-git-review.zh-CN.md)

All additions retain schema/protocol version 1 and the seven MCP signatures. Omitted options preserve strict result summaries, policy fingerprints and generated Routine text. No registry, client, Routine or switch is changed automatically.

## Registered test selection

`test_commands` accepts multiple unique IDs. Each can optionally declare `applies_to`: `"docs"`, `"code"`, `"any"`, or `{"prefixes":["docs"],"suffixes":[".md"]}`. Every allowed file must match. Prefixes use normalized directory boundaries and casefolding, suffixes are literal case-insensitive extensions; when both lists exist, both must match. Docs extensions are `.md/.rst/.txt/.adoc`; code extensions are `.py/.gd/.cs/.cpp/.c/.h/.js/.ts/.tsx/.jsx/.rs/.go`. Mixed/unknown kinds are not inferred. Commands without this metadata retain their existing behavior.

`prepare_task` warns with `test_command_scope_mismatch` on a definite mismatch; workspace `test_selection: "reject"` refuses it before creating a manifest. Default is `"warn"`. A blank command ID is `test_command_required`; the MCP parameter remains required to preserve its signature. No command is guessed or silently substituted. `task_types` still independently restricts `code_change`/`read_only`.

For a changed-file-only command, an administrator can add an **argument array**, for example `"argv":["python","-m","fictional_docs_lint","--","{changed_files}"]`. This is illustrative, not a bundled linter. Exactly one whole argument must be `{changed_files}`. Shell interpreters, batch files and interpolated placeholders are refused. Static arguments are trusted administrator configuration, never task-supplied shell text.

After edits, the local read-only helper constructs arguments from verified claim evidence and actual in-scope changes:

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.test_command_cli --registry $Registry --workspace $Workspace $ExecutionId
```

It emits `argv` as JSON and **does not execute** it, change a ledger or authorize a send. Use only an already approved native process API with `shell=False`; never join the array into a command string. Traversal, options, response-file names, control characters and shell metacharacters in expanded paths are refused. Spaces and Unicode remain individual arguments. Empty sets are refused to prevent a linter from defaulting to the entire project. No executable search or automatic fallback is introduced.

## Embedded output evidence

Optional execute-result fields:

```json
{"tests":{"command_id":"docs_lint","passed":1,"failed":0,"exit_code":0,
"output":{"head":"","tail":"","truncated":false},
"output_sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
"output_bytes":0}}
```

This is a field excerpt, not a complete result. Capture stdout/stderr in order as UTF-8 bytes. Head and tail are disjoint strings of at most 8192 UTF-8 bytes each. Hash and byte count cover the **complete** stream; `truncated` says whether any bytes were omitted. The pure `output_evidence(raw_bytes)` helper builds the three fields. Invalid UTF-8 is refused. Full output count/hash are checked when not truncated; a truncated hash is a remote assertion. All three fields must be supplied together. Existing 4 MiB JSON limits remain enforced.

Workspace `require_test_output: true` makes missing terminal output `test_evidence_missing` and prevents success. Omission/default `false` preserves existing results. Failure-before-tests may still report `not_run: true` without invented counters; requiring output does not turn that into success. Output is untrusted data, never instructions. It stays inside the private result JSON, not logs/status summaries or new `.bridge` files. Existing publication, identity and frozen-result rules apply.

With new test policy/output options enabled, status exposes authoritative neutral `tests.command_id/exit_code/passed/failed` plus safe evidence metadata; `godot_exit_code` is retained but null unless the ID is `godot` or starts with `godot_`. Legacy configurations keep their historical summary bytes, including that legacy alias. Result-file `tests` fields are authoritative in both cases. New generated execution instructions append [the optional template](../routines/test-evidence.md) only for opted-in registrations; default templates are unchanged.

## Advisory native Git review

Workspace `other_prefixes` is an optional list of normalized directory prefixes, accepted only with `git_gate: "advisory"`. The wizard accepts repeated `--other-prefix` for a new advisory registration, with no new prompt. It refuses this flag for existing registrations or read-only/generate-only operations. Prefixes label other writers; they never grant permissions or override protected paths.

Claim evidence records HEAD and pre-existing changes. Advisory snapshots additionally hash dirty files, detecting subsequent edits/removals of paths that were already dirty. Verified result/status summaries add `git_review` with `baseline_head`, `head_after`, `head_changed`, `changes_since_baseline`, `reported_changes`, `in_scope`, `declared_others`, and `out_of_scope`. The three classifications cover the union of observed changes and agent reports, with allowed paths taking precedence. `out_of_scope_changes` remains the entire outside-allowed union, including `declared_others`; declaring another writer never hides a change.

Changed HEADs are compared with native `git diff --no-renames --name-only -z`. The review is saved before the existing `git_head_mismatch` refusal: this feature does not authorize commits during execution or weaken HEAD checks. Ordinary advisory outside-scope changes still report without failing solely for scope. Failed calculated reviews are retained in frozen summaries. Historical evidence is not rewritten; old snapshots lacking dirty hashes cannot prove changes to pre-existing dirty content. This cannot attribute concurrent writers, detect out-of-scope reads or provide an OS sandbox.
