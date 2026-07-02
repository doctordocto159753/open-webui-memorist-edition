# Install Lite

Lite mode is for weak systems and local-only use.

```sh
cp .env.example .env
make dev-up-lite
```

Lite assumptions:

- SQLite only
- no FalkorDB requirement
- no embedding requirement
- lexical retrieval fallback
- small attachment budgets
- single local worker

Data lives under the configured local `MEMORIST_DB_PATH` and `MEMORIST_OBJECT_STORE`.
