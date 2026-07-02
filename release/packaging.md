# Packaging

Memorist currently supports these local packaging targets:

- Docker Compose Lite
- Docker Compose Full
- Python local development install
- Open WebUI integration bundle

Native desktop installers are not implemented and are not promised.

## Release Artifact Contents

A release folder should include:

- `docker-compose.lite.yml`
- `docker-compose.full.yml`
- `.env.example`
- Memorist Core image/build instructions
- Open WebUI filter/function files
- docs
- migration scripts
- `release/memorist-openwebui/VERSION.ijson`
- `release/package-manifest.ijson`
- package-level `CHECKSUMS`
- RC `.sha256` file

Generate manifest/checksums:

```sh
python installer/scripts/build_release_manifest.py --out release/build
python installer/scripts/assemble_rc.py
cd memorist-core && uv run python ../release/tests/rc_package_schema.py
```
