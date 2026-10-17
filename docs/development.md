# Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
python scripts/scan_release.py --root .
```

Use Python 3.12. The stdio handshake test starts `python -m grokbot_mcp.server` and expects exactly the seven tools.

Tests build a temporary git repository with a fictional counter module. They install an in-memory credential backend and a mock transport. Do not point tests at a live webhook.

The execution switch for tests is a JSON file next to the temporary registry. Production uses the same filename beside the real registry.

Build a release candidate only after the scan is clean:

```powershell
python -m pip install build==1.2.2
python scripts/build_release.py
```

The build script copies only the public files declared in `scripts/build_release.py` into a temporary directory, creates a source ZIP, builds the sdist and wheel, scans all three, and writes them with `SHA256SUMS.txt` to `dist/`. It includes current file contents, including uncommitted fixes, but never Git history. Keep older candidates outside `dist/` when preparing a new release.

For a private release audit, put a JSON array of sensitive names and identifiers in a file **outside this repository**, then pass `--markers-file <external-json-file>` to both `scan_release.py` and `build_release.py`. Never add real identities, private task ids, or secrets to the scanner source or test fixtures. With no marker file, the output explicitly says that only generic rules were used. The scanner checks literal Python strings and concatenations as well as raw text; it is not a general deobfuscator or a proof that no secrets exist.

`scan_release.py --root . --archive <source.zip> --archive <wheel.whl> --archive <sdist.tar.gz> --markers-file <external-json-file>` audits the public tree and all release artifacts without extracting archives. Private audit directories and marker files are rejected if found in the public tree or archives, even if Git ignores them. The Git ignore rules and source distribution exclusions are additional safeguards.

Historical commits require a separate privacy review before publishing Git history. A clean working tree scan or source ZIP does not sanitize an earlier commit.

Inspect `dist/` before sharing it. Do not upload it until a human has approved that exact candidate.

## Identity

Local release commits in this candidate may use a placeholder author so a private developer identity is not required. Replace that author with the public name you choose before any public push.

## Installed-wheel verification

Use an isolated installation, never the actively used MCP virtual environment. The runtime contract is Windows and Python >=3.12 with `mcp==2.3.0`. Windows CI uses Python 3.12. A run on another operating system is supplementary, not the native acceptance gate.

`.github/workflows/tests.yml` builds a wheel, installs it outside the source tree, runs the unittest suite, a fictional two-installation client check, and the memory-only demo, then scans the source tree and release archives. The QA directory has tests, scripts, examples, and packaging metadata, and no `src`. Virtual-environment and QA paths include spaces and non-ASCII characters. `wheel-summary.json` records the checkout commit, tree, and wheel SHA-256.

Security tests assert current refusal behavior: ambiguous JSON, a second claim, an unknown submission that must not be resent, a frozen result, a Git HEAD mismatch, credential presence without validity, a changed machine label, disabled writes, and path aliases. They do not need objects from any other Git history.

```powershell
python -m pip wheel . --wheel-dir C:\ExampleWheelhouse
python scripts/verify_wheel.py --wheelhouse C:\ExampleWheelhouse --output C:\ExampleEvidence
python scripts/scan_release.py --root .
```

Scan the exact wheel as well with `--archive <candidate.whl>`. Do not replace a behavior failure with an import error, a skip, or a deleted assertion. Do not treat an earlier run as evidence for a later code change.
