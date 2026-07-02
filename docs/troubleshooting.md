# Troubleshooting

## Service does not start

Run:

```sh
cd memorist-core
uv run python -m memcore.reliability check
```

## SQLite grows unexpectedly

Run WAL checkpoint outside hot paths:

```sh
uv run python -m memcore.reliability wal-checkpoint
```

`VACUUM` can reclaim space but should not run during active use.

## Graph backend problems

Use Lite mode or set `MEMORIST_GRAPH_BACKEND=disabled`. Graph projection is optional in this baseline.

## Import fails

Unsafe ZIP paths, nested archives, oversized archives, and suspicious compression ratios are rejected before extraction.
