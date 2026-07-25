from __future__ import annotations

from pathlib import Path

ROUTES = Path("memorist-core/src/memcore/api/routes_model_control.py")
PG_REPO = Path("memorist-core/src/memcore/model_control/postgres_repository.py")
STORAGE = Path("memorist-core/src/memcore/model_control/storage.py")
PIPELINE = Path("memorist-core/src/memcore/memory_worker/postgres/pipeline.py")


def test_model_control_routes_use_postgres_repository_in_full_mode() -> None:
    source = ROUTES.read_text(encoding="utf-8")
    storage_source = STORAGE.read_text(encoding="utf-8")
    assert "model_control_repository(connection)" in source
    assert 'settings.runtime_profile == "full" and settings.canonical_store == "postgres"' in (
        storage_source
    )
    assert "apply_postgres_migrations(migration_connection)" in source
    assert "return PostgresModelControlRepository(raw_connection)" in storage_source


def test_postgres_model_control_writes_profiles_and_defaults_to_canonical_tables() -> None:
    source = PG_REPO.read_text(encoding="utf-8")
    assert "INSERT INTO model_profiles" in source
    assert "INSERT INTO model_role_defaults" in source
    assert "role_default_uuid" in source
    assert "secret_env_var_name" in source
    assert "api_key" not in source.lower()


def test_model_control_default_resolution_matches_memory_worker_profile_query() -> None:
    repository_source = PG_REPO.read_text(encoding="utf-8")
    pipeline_source = PIPELINE.read_text(encoding="utf-8")
    expected_join = "JOIN model_profiles p ON p.model_profile_uuid = d.model_profile_uuid"
    assert expected_join in repository_source
    assert expected_join in pipeline_source
    assert "WHERE d.role = %s" in repository_source
    assert "WHERE d.role = 'memory_extraction'" in pipeline_source
