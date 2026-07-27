#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"

cd memorist-core

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

MEMORIST_POSTGRES_DSN=postgresql://memorist:memorist@localhost:5432/memorist_graph_test \
MEMORIST_GRAPH_BACKEND=falkordb \
MEMORIST_FALKORDB_URL=redis://localhost:6379 \
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
