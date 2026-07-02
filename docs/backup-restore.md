# Backup and Recovery

Memorist uses SQLite as the local source of truth. Backups use SQLite's backup
API and do not require external services.

## Backup

```sh
cd memorist-core
uv run python -m memcore.reliability backup \
  --db-path ./data/memorist.sqlite \
  --out ./data/backups/memorist.sqlite
```

The backup command copies the live database safely while SQLite remains in WAL
mode. Runtime object-store files should be backed up with the database if
attachments or import staging artifacts are needed.

## Interrupted Operation Recovery

```sh
uv run python -m memcore.reliability recover --db-path ./data/memorist.sqlite
uv run python -m memcore.reliability recover --db-path ./data/memorist.sqlite --yes
```

Without `--yes`, recovery prints a plan. With `--yes`, it applies safe state
transitions:

- interrupted imports move to `paused` and can be resumed
- interrupted privacy executions move to `partial_failure` for retry
- interrupted running/claimed jobs move to `dead`

## Smoke

```sh
make recovery-tests
```

The smoke creates interrupted import, privacy, and job states, verifies the
planned recovery, applies it, and confirms that a second recovery pass has no
remaining actions.

