## Directory authorization

The local profile embeds `allowed_prefixes` and `policy_fingerprint`; it does not
embed the full registered file list. Check that both match the local registration
and binding before proceeding. Forbidden/protected files and every `.git` or
`.bridge` path segment remain excluded. This is not an OS sandbox.

Validate the manifest's exact task list against the registered file/prefix policy
using the local bridge. Execute uses only the exact `allowed_files` in the successful
claim JSON. A directory grant is never permission to edit all its files. Files in
`scope_expansion.create_files` may be absent at claim time; do not create them just
to satisfy a check. Report only measured `changed_files`. Declared files left absent
are reported by the bridge as `not_created` and do not alone cause failure.
<!-- prefix-ceiling -->
## Directory authorization ceiling

The local profile embeds `prefix_ceiling` and `policy_fingerprint`. Verify both
against the local registration and binding. Forbidden/protected paths and all
`.git` / `.bridge` segments remain excluded. This is not an OS sandbox.

Validation uses the manifest's frozen `active_prefixes` and exact task list through
the local bridge. Execution uses `active_prefixes` and `active_prefixes_sha256`
from the successful claim JSON, never the latest registry's active prefixes.
The digest is SHA-256 of UTF-8 JSON with sorted keys, ASCII escaping and compact
comma/colon separators. Every active prefix must equal or descend from one ceiling
prefix, with its extensions a subset of that same entry. If the claim grant exceeds
the embedded ceiling, or its digest does not match, STOP. A missing successful
claim is never permission to execute. Exact registered `allowed_files` exceptions
remain independent of the ceiling, but cannot bypass forbidden/protected paths.

Edit only the exact `allowed_files` returned by the claim. A prefix grant is not
permission to edit all files in a directory. `create_files` may be absent at claim
time; do not create them to satisfy a check. Report measured `changed_files`;
declared files left absent are reported as `not_created` and do not alone fail.
Changing the ceiling or forbidden/protected policy requires updated Routine text
and binding confirmation; changing active prefixes within the ceiling does not.
