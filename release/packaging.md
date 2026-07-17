# Packaging

Memorist currently supports these local packaging targets:

- Docker Compose Lite
- Docker Compose Full
- Python local development install
- Derivative Open WebUI product image (`release/openwebui-image/`)

Native desktop installers are not implemented and are not promised.

## Release Artifact Contents

The user-facing ZIP (`release/rc/memorist-openwebui-<version>.zip`) extracts
to a single root directory containing:

- `Memorist.cmd` and the PowerShell lifecycle scripts (Install/Start/Stop/
  Restart/Show-Logs/Reset-Data/Uninstall/Test-Full)
- `compose.yml`, `compose.lite.yml`, `compose.full.yml`
- `runtime/` — Memorist Core build context, the Open WebUI integration
  package, and the derivative Open WebUI image build context
  (`runtime/openwebui-image/`: Dockerfile, pinned source manifest, frontend
  overlay, patch layer)
- `docs/`
- `.env.example`, `VERSION.ijson`
- `checksums.sha256`, `package-manifest.ijson`

## Integrity layering

Integrity metadata is layered with one clear authority per layer and no
self-hashing cycle:

1. **`checksums.sha256`** (inner layer) covers every shipped file except
   itself, `package-manifest.ijson`, and runtime data folders. It is generated
   first, with newline normalization so Windows and Linux verify identically.
2. **`package-manifest.ijson`** (outer layer) is generated **last**, over the
   final tree, and truthfully covers everything — including
   `checksums.sha256` — excluding only itself. Nothing is written into the
   staging tree after the manifest.
3. **The RC `.sha256` sidecar** covers the whole ZIP.

`installer/scripts/validate_package.py` extracts the final ZIP into a path
containing spaces and verifies the required root layout, every declared hash
and size, the absence of undeclared files, checksum coverage with no stale
entries, and the forbidden-content scan. CI also proves the validator fails
on a tampered archive.

Generate and validate:

```sh
python installer/scripts/assemble_rc.py
python installer/scripts/validate_package.py release/rc/memorist-openwebui-<version>.zip
python release/tests/rc_package_schema.py
python release/tests/version_consistency.py
python release/tests/upgrade_contract.py
```
