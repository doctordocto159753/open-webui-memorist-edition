CORE_DIR := memorist-core
UV ?= python -m uv
PYTHON ?= python
REPO_ROOT := .
RELEASE_REPORT := cd $(CORE_DIR) && $(UV) run python -c "import sys; sys.path.insert(0, '..'); from release.tests.report import main; main()"

.PHONY: install check release-check dev test lint typecheck format test-security model-control-tests memory-worker-prompt-pack-test version-consistency source-package source-tree-scan baseline-check full-mode-check clean-artifacts perf-smoke perf-local reliability-check release-manifest smoke-lite smoke-full smoke-release smoke-daily smoke-import-heavy-ci smoke-import-heavy-small smoke-import-heavy-local heritage-roundtrip forget-residue consistency-check recovery-tests openwebui-contract-tests openwebui-container-smoke rc-schema-test p2-check assemble-rc dev-up-lite dev-up-full

install:
	cd $(CORE_DIR) && $(UV) sync --dev

check:
	cd $(CORE_DIR) && $(UV) sync --all-extras --dev
	cd $(CORE_DIR) && $(UV) run ruff check .
	cd $(CORE_DIR) && $(UV) run mypy src/memcore
	cd $(CORE_DIR) && $(UV) run pytest -q

release-check: check model-control-tests memory-worker-prompt-pack-test smoke-daily smoke-import-heavy-ci heritage-roundtrip forget-residue consistency-check recovery-tests openwebui-contract-tests assemble-rc rc-schema-test version-consistency source-package source-tree-scan
	$(PYTHON) -m release.scan_forbidden_files release/rc/memorist-openwebui-0.2.0-beta.2.zip
	$(RELEASE_REPORT) --manifest release/test_manifest.ijson --external-gates-passed

dev:
	cd $(CORE_DIR) && $(UV) run uvicorn memcore.main:app --host 0.0.0.0 --port 8777 --reload

test:
	cd $(CORE_DIR) && $(UV) run pytest

lint:
	cd $(CORE_DIR) && $(UV) run ruff check .

typecheck:
	cd $(CORE_DIR) && $(UV) run mypy src tests

test-security:
	cd $(CORE_DIR) && $(UV) run pytest tests/test_phase6_security.py

model-control-tests:
	cd $(CORE_DIR) && $(UV) run pytest tests/test_model_control_plane.py -q

memory-worker-prompt-pack-test:
	cd $(CORE_DIR) && $(UV) run pytest tests/test_memory_worker_prompt_pack.py -q

version-consistency:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/version_consistency.py

source-package:
	$(PYTHON) release/source_package.py --out release/source/open-webui-memorist-edition-source.zip

source-tree-scan:
	$(PYTHON) -m release.scan_source_tree release/source/open-webui-memorist-edition-source.zip

baseline-check:
	$(PYTHON) scripts/baseline_check.py

full-mode-check:
	$(PYTHON) scripts/full_mode_check.py

clean-artifacts:
	$(PYTHON) scripts/clean_artifacts.py --apply

perf-smoke:
	cd $(CORE_DIR) && $(UV) run python -m memcore.performance perf-smoke --profile lite

perf-local:
	cd $(CORE_DIR) && $(UV) run python -m memcore.performance perf-local --profile standard

reliability-check:
	cd $(CORE_DIR) && $(UV) run python -m memcore.reliability check

release-manifest:
	$(PYTHON) installer/scripts/build_release_manifest.py --out release/build

smoke-lite:
	$(RELEASE_REPORT)

smoke-full:
	$(RELEASE_REPORT)

smoke-release:
	$(RELEASE_REPORT)

smoke-daily:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/daily_use_smoke.py

smoke-import-heavy-ci:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/heavy_import_smoke.py --mode ci-small --max-seconds 90

smoke-import-heavy-small:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/heavy_import_smoke.py --mode small-heavy

smoke-import-heavy-local:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/heavy_import_smoke.py --mode local-heavy

heritage-roundtrip:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/heritage_roundtrip.py

forget-residue:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/forget_residue.py

consistency-check:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/consistency_check.py

recovery-tests:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/recovery_tests.py

openwebui-contract-tests:
	cd $(CORE_DIR) && $(UV) run pytest ../open-webui-integration/memorist/tests -q

openwebui-container-smoke:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/openwebui_container_smoke.py

rc-schema-test:
	cd $(CORE_DIR) && $(UV) run python ../release/tests/rc_package_schema.py

p2-check: check smoke-daily smoke-import-heavy-ci heritage-roundtrip forget-residue consistency-check recovery-tests openwebui-contract-tests

assemble-rc:
	$(PYTHON) installer/scripts/assemble_rc.py

format:
	cd $(CORE_DIR) && $(UV) run ruff format .
	cd $(CORE_DIR) && $(UV) run ruff check . --fix

dev-up-lite:
	docker compose -f docker-compose.lite.yml up --build

dev-up-full:
	docker compose -f docker-compose.full.yml up --build
