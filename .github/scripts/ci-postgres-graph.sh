#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${MEMORIST_POSTGRES_DSN:?MEMORIST_POSTGRES_DSN is required}"

cd memorist-core

# The complete suite contains both Lite-default tests and PostgreSQL-gated tests.
# Keep only the DSN globally so the latter execute without forcing every default
# test into Full mode. Full/graph settings are applied only to the graph command.
unset MEMORIST_RUNTIME_PROFILE
unset MEMORIST_CANONICAL_STORE
unset MEMORIST_GRAPH_BACKEND
unset MEMORIST_FALKORDB_URL
unset MEMORIST_ALLOW_FULL_GRAPH_DEGRADED
unset MEMORIST_HOT_SCHEDULER
unset MEMORIST_OBJECT_STORE_PATH
unset MEMORIST_REAL_FALKORDB

uv run pytest -q --tb=short \
  --junitxml="${RUNNER_TEMP}/postgres-results.xml"

uv run python - <<'PY'
import psycopg

with psycopg.connect(
    "postgresql://memorist:memorist@localhost:5432/postgres",
    autocommit=True,
) as connection:
    connection.execute("DROP DATABASE IF EXISTS memorist_graph_test WITH (FORCE)")
    connection.execute("CREATE DATABASE memorist_graph_test")
PY

MEMORIST_RUNTIME_PROFILE=full \
MEMORIST_CANONICAL_STORE=postgres \
MEMORIST_POSTGRES_DSN=postgresql://memorist:memorist@localhost:5432/memorist_graph_test \
MEMORIST_GRAPH_BACKEND=falkordb \
MEMORIST_FALKORDB_URL=redis://localhost:6379 \
MEMORIST_ALLOW_FULL_GRAPH_DEGRADED=true \
MEMORIST_HOT_SCHEDULER=in_memory \
MEMORIST_OBJECT_STORE_PATH=/tmp/memorist-graph-objects \
MEMORIST_REAL_FALKORDB=1 \
uv run pytest tests/test_memory_control_contract_full.py \
  -k "full_graph_retrieval or full_graph_outage" -q --tb=short \
  --junitxml="${RUNNER_TEMP}/graph-results.xml"

uv run python - <<'PY'
import os
import xml.etree.ElementTree as ET

path = os.path.join(os.environ["RUNNER_TEMP"], "graph-results.xml")
root = ET.parse(path).getroot()
cases = list(root.iter("testcase"))
skipped = list(root.iter("skipped"))
assert len(cases) == 2 and not skipped, (len(cases), len(skipped))
PY
