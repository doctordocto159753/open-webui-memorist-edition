#!/usr/bin/env bash
set +e

UV_BIN="${UV_BIN:-uv}"
"$UV_BIN" sync --all-extras --quiet
"$UV_BIN" run pytest tests/test_pr4d_semantic_baseline.py -q --tb=short >/tmp/pr4d-full.log 2>&1
status=$?
echo "FULL_BASELINE=$status"
cat /tmp/pr4d-full.log
exit "$status"
