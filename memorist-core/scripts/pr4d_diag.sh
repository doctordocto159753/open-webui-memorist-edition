#!/usr/bin/env bash
set +e

UV_BIN="${UV_BIN:-uv}"
"$UV_BIN" sync --all-extras --quiet

TARGETS="src/memcore/memory_worker/semantic src/memcore/memory_worker/routing src/memcore/memory_worker/postgres/deterministic_fallback.py src/memcore/memory_worker/postgres/routing_policy_adapter.py src/memcore/memory_worker/postgres/gated_candidate_adapter.py tests/test_pr4d_semantic_baseline.py tests/test_pr4d_canonical_semantic_contract.py tests/test_pr4d_semantic_factor_resolver.py tests/test_pr4d_shared_routing_policy.py tests/test_pr4d_gate_candidate_policy.py"

"$UV_BIN" run ruff check $TARGETS > /tmp/pr4d-ruff-check.log 2>&1
ruff_check=$?
echo "=== ruff check status: $ruff_check ==="
tail -n 80 /tmp/pr4d-ruff-check.log

"$UV_BIN" run ruff format --check $TARGETS > /tmp/pr4d-ruff-format.log 2>&1
ruff_format=$?
echo "=== ruff format status: $ruff_format ==="
tail -n 80 /tmp/pr4d-ruff-format.log

"$UV_BIN" run mypy $TARGETS > /tmp/pr4d-mypy.log 2>&1
mypy_status=$?
echo "=== mypy status: $mypy_status ==="
tail -n 120 /tmp/pr4d-mypy.log

"$UV_BIN" run pytest tests/test_pr4d_semantic_baseline.py -q --tb=short > /tmp/pr4d-full.log 2>&1
full_status=$?
echo "=== full baseline status: $full_status ==="
tail -n 160 /tmp/pr4d-full.log

if [ "$ruff_check" -ne 0 ] || [ "$ruff_format" -ne 0 ] || [ "$mypy_status" -ne 0 ] || [ "$full_status" -ne 0 ]; then
  exit 1
fi
