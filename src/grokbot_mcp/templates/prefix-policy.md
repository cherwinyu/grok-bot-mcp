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
