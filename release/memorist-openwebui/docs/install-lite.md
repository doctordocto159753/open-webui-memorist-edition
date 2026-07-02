# Install Lite

Lite starts `memorist-core` and `open-webui` with SQLite and local object-store volumes. It does not require FalkorDB or embeddings.

```bash
scripts/start-lite.sh
```

Data volumes:

- `memorist-data`: SQLite
- `memorist-objects`: local objects
- `memorist-import-staging`: staged imports
- `memorist-exports`: backups and exports
- `openwebui-data`: Open WebUI state
