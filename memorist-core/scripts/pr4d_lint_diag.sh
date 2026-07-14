#!/usr/bin/env bash
set +e

UV_BIN="${UV_BIN:-uv}"
"$UV_BIN" sync --all-extras --quiet

TARGETS="src/memcore/memory_worker/semantic src/memcore/memory_worker/routing src/memcore/memory_worker/postgres/deterministic_fallback.py src/memcore/memory_worker/postgres/routing_policy_adapter.py src/memcore/memory_worker/postgres/gated_candidate_adapter.py tests/test_pr4d_semantic_baseline.py tests/test_pr4d_canonical_semantic_contract.py tests/test_pr4d_semantic_factor_resolver.py tests/test_pr4d_shared_routing_policy.py tests/test_pr4d_gate_candidate_policy.py"

"$UV_BIN" run ruff check $TARGETS >/tmp/pr4d-ruff-check.log 2>&1
ruff_check=$?
"$UV_BIN" run ruff format --check $TARGETS >/tmp/pr4d-ruff-format.log 2>&1
ruff_format=$?
"$UV_BIN" run mypy $TARGETS >/tmp/pr4d-mypy.log 2>&1
mypy_status=$?

echo "RUFF_CHECK=$ruff_check RUFF_FORMAT=$ruff_format MYPY=$mypy_status"
if [ "$ruff_check" -ne 0 ]; then
  echo "=== ruff check ==="
  cat /tmp/pr4d-ruff-check.log
fi
if [ "$ruff_format" -ne 0 ]; then
  echo "=== ruff format ==="
  cat /tmp/pr4d-ruff-format.log
fi
if [ "$mypy_status" -ne 0 ]; then
  echo "=== mypy ==="
  cat /tmp/pr4d-mypy.log
fi

[ "$ruff_check" -eq 0 ] && [ "$ruff_format" -eq 0 ] && [ "$mypy_status" -eq 0 ]
