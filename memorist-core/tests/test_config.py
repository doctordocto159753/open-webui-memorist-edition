from pathlib import Path

import pytest
from pydantic import ValidationError

from memcore.api.routes_config import get_effective_configuration, get_version
from memcore.config import (
    REDACTED_VALUE,
    Settings,
    get_effective_config,
    get_settings,
    redact_secrets,
)
from memcore.main import create_app
from memcore.version import SCHEMA_VERSION, __version__

MEMORIST_ENV_KEYS = (
    "MEMORIST_ENV",
    "MEMORIST_LOCAL_ONLY",
    "MEMORIST_HOST",
    "MEMORIST_PORT",
    "MEMORIST_RUNTIME_PROFILE",
    "MEMORIST_CANONICAL_STORE",
    "MEMORIST_DB_PATH",
    "MEMORIST_POSTGRES_DSN",
    "MEMORIST_OBJECT_STORE_PATH",
    "MEMORIST_GRAPH_BACKEND",
    "MEMORIST_FALKORDB_URL",
    "MEMORIST_ALLOW_FULL_GRAPH_DEGRADED",
    "MEMORIST_VECTOR_BACKEND",
    "MEMORIST_HOT_SCHEDULER",
    "MEMORIST_SCHEDULER_MAX_CONSECUTIVE_LOW_PRIORITY",
    "MEMORIST_IMPORT_BATCH_MAX_MS",
    "MEMORIST_HOT_LANE_TARGET_WAIT_MS",
    "MEMORIST_PREFLIGHT_PERSIST_MAX_WAIT_MS",
    "MEMORIST_LOG_LEVEL",
    "MEMORIST_ENABLE_OPENWEBUI_ADAPTER",
    "MEMORIST_ENABLE_GRAPH_PROJECTION",
    "MEMORIST_ENABLE_MEMORY_WORKER",
    "MEMORIST_ENABLE_IMPORT_SYSTEM",
    "MEMORIST_ENABLE_MEMORY_CONTEXT_ATTACHMENT",
    "MEMORIST_ENABLE_ACTIVE_MEMORY_BLOCKS",
    "MEMORIST_ENABLE_USER_PROFILE_PROJECTION",
    "MEMORIST_ENABLE_FORGETTING_PIPELINE",
    "MEMORIST_PREFLIGHT_ENABLED",
    "MEMORIST_PREFLIGHT_TIMEOUT_MS",
    "MEMORIST_PREFLIGHT_MODEL_TIMEOUT_MS",
    "MEMORIST_PROCESSING_NODE_DEFAULT_TIMEOUT_MS",
    "MEMORIST_MEMORY_EXTRACTION_TIMEOUT_MS",
    "MEMORIST_PROCESSING_CONTEXT_LIMIT_TOKENS",
    "MEMORIST_PROCESSING_MAX_INPUT_TOKENS",
    "MEMORIST_RETRIEVAL_MODE",
    "MEMORIST_ATTACHMENT_TOKEN_BUDGET",
    "MEMORIST_FAIL_OPEN",
    "MEMORIST_ACTOR_ASSERTION_SECRET",
    "MEMORIST_ACTOR_SERVICE_TOKEN",
    "MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS",
)


def clear_memorist_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in MEMORIST_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_version_endpoint() -> None:
    assert get_version() == {
        "service": "memorist-core",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "runtime_profile": "lite",
        "canonical_store": "sqlite",
        "graph_backend": "disabled",
        "hot_scheduler": "disabled",
        "profile_warnings": [],
    }


def test_settings_defaults_are_local_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.env == "development"
    assert settings.local_only is True
    assert settings.host == "0.0.0.0"
    assert settings.port == 8777
    assert settings.runtime_profile == "lite"
    assert settings.canonical_store == "sqlite"
    assert settings.db_path == "./data/memorist.sqlite"
    assert settings.postgres_dsn is None
    assert settings.object_store_path == "./data/objects"
    assert settings.graph_backend == "disabled"
    assert settings.falkordb_url is None
    assert settings.vector_backend == "disabled"
    assert settings.hot_scheduler == "disabled"
    assert settings.log_level == "INFO"
    assert settings.enable_openwebui_adapter is True
    assert settings.enable_graph_projection is False
    assert settings.enable_memory_worker is False
    assert settings.enable_import_system is False
    assert settings.enable_memory_context_attachment is False
    assert settings.enable_active_memory_blocks is False
    assert settings.enable_user_profile_projection is False
    assert settings.enable_forgetting_pipeline is False


def test_environment_variables_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "sqlite" / "memorist.sqlite"
    object_store_path = tmp_path / "objects"

    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_HOST", "127.0.0.1")
    monkeypatch.setenv("MEMORIST_PORT", "9000")
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "dev")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
    monkeypatch.setenv("MEMORIST_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(object_store_path))
    monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "falkordb_lite")
    monkeypatch.setenv("MEMORIST_VECTOR_BACKEND", "sqlite_vec")
    monkeypatch.setenv("MEMORIST_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("MEMORIST_ENABLE_GRAPH_PROJECTION", "true")
    monkeypatch.setenv("MEMORIST_ENABLE_MEMORY_WORKER", "true")

    settings = Settings()

    assert settings.env == "test"
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.runtime_profile == "dev"
    assert settings.canonical_store == "sqlite"
    assert settings.db_path == str(db_path)
    assert settings.object_store_path == str(object_store_path)
    assert settings.graph_backend == "falkordb_lite"
    assert settings.vector_backend == "sqlite_vec"
    assert settings.log_level == "ERROR"
    assert settings.enable_graph_projection is True
    assert settings.enable_memory_worker is True


def test_local_config_file_can_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MEMORIST_PORT=9001\n", encoding="utf-8")

    settings = Settings()

    assert settings.port == 9001


def test_full_missing_graph_url_is_explicitly_degraded_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("MEMORIST_FALKORDB_URL", "")
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn="postgresql://memorist:test@localhost/memorist",
        hot_scheduler="in_memory",
        allow_full_graph_degraded=True,
    )
    assert settings.falkordb_url is None


def test_invalid_port_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_PORT", "70000")

    with pytest.raises(ValidationError):
        Settings()


def test_local_only_false_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_LOCAL_ONLY", "false")

    with pytest.raises(ValidationError, match="local_only=false"):
        Settings()


def test_app_fails_fast_on_invalid_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_LOCAL_ONLY", "false")
    get_settings.cache_clear()

    with pytest.raises(ValidationError, match="local_only=false"):
        create_app()

    get_settings.cache_clear()


def test_effective_config_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    assert get_effective_configuration() == {
        "env": "development",
        "local_only": True,
        "runtime_profile": "lite",
        "canonical_store": "sqlite",
        "graph_backend": "disabled",
        "hot_scheduler": "disabled",
        "profile_warnings": [],
        "db_path": "./data/memorist.sqlite",
        "postgres_dsn_configured": False,
        "object_store_path": "./data/objects",
        "vector_backend": "disabled",
        "features": {
            "openwebui_adapter": True,
            "graph_projection": False,
            "memory_worker": False,
            "import_system": False,
            "memory_context_attachment": False,
            "active_memory_blocks": False,
            "user_profile_projection": False,
            "forgetting_pipeline": False,
        },
        "preflight": {
            "enabled": True,
            "timeout_ms": 60_000,
            "model_timeout_ms": 60_000,
            "retrieval_mode": "standard",
            "attachment_token_budget": REDACTED_VALUE,
            "attachment_max_tokens": REDACTED_VALUE,
            "attachment_context_ratio": 0.1,
            "attachment_min_tokens": REDACTED_VALUE,
            "attachment_reserved_completion_tokens": REDACTED_VALUE,
            "attachment_fallback_context_window": 8192,
            "unknown_model_context_window": 8192,
            "attachment_safety_margin_tokens": REDACTED_VALUE,
            "fail_open": True,
        },
        "processing_nodes": {
            "default_timeout_ms": 120_000,
            "effective_timeouts_ms": {
                "memory_extraction": 120_000,
                "high_confidence_extraction": 120_000,
                "privacy_sensitivity": 120_000,
                "block_compaction": 120_000,
                "import_reconstruction": 120_000,
                "embedding": 120_000,
            },
            "context_limit_tokens": REDACTED_VALUE,
            "max_input_tokens": REDACTED_VALUE,
            "effective_default_input_limit_tokens": REDACTED_VALUE,
            "max_output_tokens": REDACTED_VALUE,
            "embedding": {
                "max_input_tokens": REDACTED_VALUE,
                "max_batch_tokens": REDACTED_VALUE,
            },
        },
        "imports": {
            "batch_size": 100,
            "batch_max_ms": 250,
            "max_jobs_per_minute": 60,
            "max_write_queue_depth": 500,
            "low_priority": True,
            "reconstruction_default": "off",
            "reconstruction_worker": {
                "enabled": True,
                "deployment_model": "fastapi_lifespan_background_service",
                "concurrency": 2,
                "poll_seconds": 2,
                "lease_seconds": 300,
                "heartbeat_seconds": 60,
                "max_attempts": 5,
                "allow_deterministic_fallback": True,
                "retry_base_seconds": 10,
                "retry_max_seconds": 900,
            },
        },
        "scheduler": {
            "backend": "disabled",
            "max_consecutive_low_priority": 1,
            "hot_lane_target_wait_ms": 100,
            "preflight_persist_max_wait_ms": 250,
        },
    }


def test_processing_timeout_resolution_and_input_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_PROCESSING_NODE_DEFAULT_TIMEOUT_MS", "130000")
    monkeypatch.setenv("MEMORIST_MEMORY_EXTRACTION_TIMEOUT_MS", "140000")

    settings = Settings()

    assert settings.processing_timeout_ms("memory_extraction") == 140_000
    assert settings.processing_timeout_ms("embedding") == 130_000
    assert (
        settings.processing_input_limit_tokens(
            certified_context_window=80_000,
            provider_limit=90_000,
            role_safety_limit=70_000,
        )
        == 70_000
    )


def test_effective_config_redacts_secret_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    settings = Settings()
    effective_config = get_effective_config(settings)
    redacted_config = redact_secrets(
        {
            **effective_config,
            "api_key": "not exposed",
            "nested": {
                "access_token": "not exposed",
                "client_secret": "not exposed",
                "safe_value": "visible",
            },
        }
    )

    assert redacted_config["api_key"] == REDACTED_VALUE
    assert redacted_config["nested"]["access_token"] == REDACTED_VALUE
    assert redacted_config["nested"]["client_secret"] == REDACTED_VALUE
    assert redacted_config["nested"]["safe_value"] == "visible"


def test_object_store_directory_is_created(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    object_store_path = tmp_path / "objects" / "nested"
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(object_store_path))

    Settings()

    assert object_store_path.is_dir()


def test_sqlite_parent_directory_is_created(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "sqlite" / "memorist.sqlite"
    monkeypatch.setenv("MEMORIST_DB_PATH", str(db_path))

    Settings()

    assert db_path.parent.is_dir()


def test_production_cannot_use_debug_log_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_ENV", "production")
    monkeypatch.setenv("MEMORIST_LOG_LEVEL", "DEBUG")

    with pytest.raises(ValidationError, match="log_level=DEBUG"):
        Settings()


def test_production_actor_secrets_must_be_distinct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_ENV", "production")
    monkeypatch.setenv("MEMORIST_ACTOR_ASSERTION_SECRET", "same-secret")
    monkeypatch.setenv("MEMORIST_ACTOR_SERVICE_TOKEN", "same-secret")

    with pytest.raises(ValidationError, match="must be distinct"):
        Settings()


def test_full_profile_requires_postgres_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "full")

    with pytest.raises(
        ValidationError,
        match="runtime_profile=full requires canonical_store=postgres",
    ):
        Settings()


def test_full_profile_loads_with_explicit_postgres_and_falkordb(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "postgres")
    monkeypatch.setenv("MEMORIST_POSTGRES_DSN", "postgresql://memorist:secret@localhost/memorist")
    monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("MEMORIST_FALKORDB_URL", "redis://localhost:6379")
    monkeypatch.setenv("MEMORIST_HOT_SCHEDULER", "in_memory")

    settings = Settings()

    assert settings.runtime_profile == "full"
    assert settings.canonical_store == "postgres"
    assert settings.graph_backend == "falkordb"
    assert settings.hot_scheduler == "in_memory"
    assert get_effective_config(settings)["postgres_dsn_configured"] is True


def test_production_full_profile_rejects_disabled_runtime_features(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clear_memorist_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORIST_ENV", "production")
    monkeypatch.setenv("MEMORIST_ACTOR_ASSERTION_SECRET", "assertion-secret")
    monkeypatch.setenv("MEMORIST_ACTOR_SERVICE_TOKEN", "service-token")
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "postgres")
    monkeypatch.setenv("MEMORIST_POSTGRES_DSN", "postgresql://memorist:secret@localhost/memorist")
    monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("MEMORIST_FALKORDB_URL", "redis://localhost:6379")
    monkeypatch.setenv("MEMORIST_HOT_SCHEDULER", "in_memory")

    with pytest.raises(ValidationError, match="production full runtime requires enabled features"):
        Settings()
