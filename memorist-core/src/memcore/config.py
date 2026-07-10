from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GraphBackend = Literal["disabled", "falkordb", "falkordb_lite"]
VectorBackend = Literal["disabled", "sqlite_vec", "falkordb", "qdrant_local", "lancedb"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
RetrievalModeSetting = Literal["lite", "standard", "full", "debug"]
RuntimeProfile = Literal["lite", "full", "dev"]
CanonicalStoreKind = Literal["sqlite", "postgres"]
HotSchedulerBackend = Literal["disabled", "in_memory"]

SECRET_KEY_PARTS = ("key", "token", "secret", "password", "credential")
REDACTED_VALUE = "[redacted]"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        env_prefix="MEMORIST_",
        extra="ignore",
    )

    env: str = "development"
    local_only: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=8777, ge=1, le=65535)
    runtime_profile: RuntimeProfile = "lite"
    canonical_store: CanonicalStoreKind = "sqlite"
    db_path: str = "./data/memorist.sqlite"
    postgres_dsn: str | None = None
    object_store_path: str = "./data/objects"
    graph_backend: GraphBackend = "disabled"
    falkordb_url: str | None = None
    allow_full_graph_degraded: bool = False
    vector_backend: VectorBackend = "disabled"
    hot_scheduler: HotSchedulerBackend = "disabled"
    scheduler_max_consecutive_low_priority: int = Field(default=1, ge=1)
    import_batch_max_ms: int = Field(default=250, ge=1)
    hot_lane_target_wait_ms: int = Field(default=100, ge=1)
    preflight_persist_max_wait_ms: int = Field(default=250, ge=1)
    log_level: LogLevel = "INFO"
    enable_openwebui_adapter: bool = True
    enable_graph_projection: bool = False
    enable_memory_worker: bool = False
    enable_import_system: bool = False
    enable_memory_context_attachment: bool = False
    enable_active_memory_blocks: bool = False
    enable_user_profile_projection: bool = False
    enable_forgetting_pipeline: bool = False
    preflight_enabled: bool = True
    preflight_timeout_ms: int = Field(default=500, ge=1)
    preflight_model_timeout_ms: int = Field(default=800, ge=1)
    preflight_fail_open: bool = True
    retrieval_mode: RetrievalModeSetting = "standard"
    attachment_token_budget: int = Field(default=1800, ge=1)
    attachment_max_tokens: int = Field(default=1800, ge=0)
    attachment_context_ratio: float = Field(default=0.10, gt=0.0, le=1.0)
    attachment_min_tokens: int = Field(default=256, ge=1)
    attachment_reserved_completion_tokens: int = Field(default=1024, ge=0)
    attachment_fallback_context_window: int = Field(default=8192, ge=1024)
    unknown_model_context_window: int = Field(default=8192, ge=1024)
    attachment_safety_margin_tokens: int = Field(default=256, ge=0)
    import_batch_size: int = Field(default=100, ge=1, le=5000)
    import_max_jobs_per_minute: int = Field(default=60, ge=1)
    import_max_write_queue_depth: int = Field(default=500, ge=1)
    import_low_priority: bool = True
    import_reconstruction_default: str = "off"
    import_reconstruction_worker_enabled: bool = True
    import_reconstruction_concurrency: int = Field(default=1, ge=1, le=16)
    import_reconstruction_worker_batch_size: int = Field(default=5, ge=1, le=100)
    import_reconstruction_lease_seconds: int = Field(default=300, ge=10)
    import_reconstruction_max_attempts: int = Field(default=5, ge=1, le=20)
    import_reconstruction_retry_base_seconds: int = Field(default=10, ge=1)
    import_reconstruction_poll_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    fail_open: bool = True

    @field_validator("falkordb_url", mode="before")
    @classmethod
    def empty_falkordb_url_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("postgres_dsn", mode="before")
    @classmethod
    def empty_postgres_dsn_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("db_path")
    @classmethod
    def ensure_sqlite_parent_is_creatable(cls, value: str) -> str:
        parent = Path(value).expanduser().parent
        if parent != Path("."):
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ValueError(f"db_path parent directory is not creatable: {parent}") from error
        return value

    @field_validator("object_store_path")
    @classmethod
    def ensure_object_store_is_creatable(cls, value: str) -> str:
        object_store = Path(value).expanduser()
        try:
            object_store.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(
                f"object_store_path directory is not creatable: {object_store}"
            ) from error
        return value

    @model_validator(mode="after")
    def validate_runtime_policy(self) -> "Settings":
        if not self.local_only:
            raise ValueError("local_only=false is not supported by this local-first release")
        if self.graph_backend == "falkordb" and not self.falkordb_url:
            raise ValueError("falkordb_url is required when graph_backend=falkordb")
        if self.env.lower() == "production" and self.log_level == "DEBUG":
            raise ValueError("log_level=DEBUG is not allowed in production")
        if self.runtime_profile == "lite":
            if self.canonical_store != "sqlite":
                raise ValueError("runtime_profile=lite requires canonical_store=sqlite")
            if self.graph_backend not in {"disabled", "falkordb_lite"}:
                raise ValueError("runtime_profile=lite requires graph_backend=disabled")
        if self.runtime_profile == "full":
            if self.canonical_store != "postgres":
                raise ValueError("runtime_profile=full requires canonical_store=postgres")
            if not self.postgres_dsn:
                raise ValueError("postgres_dsn is required when canonical_store=postgres")
            if self.graph_backend != "falkordb" and not self.allow_full_graph_degraded:
                raise ValueError(
                    "runtime_profile=full requires graph_backend=falkordb unless "
                    "allow_full_graph_degraded=true"
                )
            if self.hot_scheduler != "in_memory":
                raise ValueError("runtime_profile=full requires hot_scheduler=in_memory")
        if (
            self.runtime_profile == "dev"
            and self.canonical_store == "postgres"
            and not self.postgres_dsn
        ):
            raise ValueError("postgres_dsn is required when canonical_store=postgres")
        return self

    def profile_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.runtime_profile == "dev":
            warnings.append("dev_profile_not_for_release")
        if self.runtime_profile == "full" and self.graph_backend != "falkordb":
            warnings.append("full_graph_degraded")
        if self.runtime_profile == "lite" and self.graph_backend == "falkordb_lite":
            warnings.append("falkordb_lite_is_noncanonical_preview")
        return warnings

    def runtime_summary(self) -> dict[str, Any]:
        return {
            "runtime_profile": self.runtime_profile,
            "canonical_store": self.canonical_store,
            "graph_backend": self.graph_backend,
            "hot_scheduler": self.hot_scheduler,
            "profile_warnings": self.profile_warnings(),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_secret_key(key: str) -> bool:
    normalized_key = key.lower()
    return any(secret_part in normalized_key for secret_part in SECRET_KEY_PARTS)


def redact_secrets(config: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in config.items():
        if is_secret_key(key):
            redacted[key] = REDACTED_VALUE
        elif isinstance(value, Mapping):
            redacted[key] = redact_secrets(value)
        else:
            redacted[key] = value
    return redacted


def get_effective_config(settings: Settings) -> dict[str, Any]:
    config = {
        "env": settings.env,
        "local_only": settings.local_only,
        **settings.runtime_summary(),
        "db_path": settings.db_path,
        "postgres_dsn_configured": settings.postgres_dsn is not None,
        "object_store_path": settings.object_store_path,
        "vector_backend": settings.vector_backend,
        "features": {
            "openwebui_adapter": settings.enable_openwebui_adapter,
            "graph_projection": settings.enable_graph_projection,
            "memory_worker": settings.enable_memory_worker,
            "import_system": settings.enable_import_system,
            "memory_context_attachment": settings.enable_memory_context_attachment,
            "active_memory_blocks": settings.enable_active_memory_blocks,
            "user_profile_projection": settings.enable_user_profile_projection,
            "forgetting_pipeline": settings.enable_forgetting_pipeline,
        },
        "preflight": {
            "enabled": settings.preflight_enabled,
            "timeout_ms": settings.preflight_timeout_ms,
            "model_timeout_ms": settings.preflight_model_timeout_ms,
            "retrieval_mode": settings.retrieval_mode,
            "attachment_token_budget": settings.attachment_token_budget,
            "attachment_max_tokens": settings.attachment_max_tokens,
            "attachment_context_ratio": settings.attachment_context_ratio,
            "attachment_min_tokens": settings.attachment_min_tokens,
            "attachment_reserved_completion_tokens": settings.attachment_reserved_completion_tokens,
            "attachment_fallback_context_window": settings.attachment_fallback_context_window,
            "unknown_model_context_window": settings.unknown_model_context_window,
            "attachment_safety_margin_tokens": settings.attachment_safety_margin_tokens,
            "fail_open": settings.preflight_fail_open,
        },
        "imports": {
            "batch_size": settings.import_batch_size,
            "batch_max_ms": settings.import_batch_max_ms,
            "max_jobs_per_minute": settings.import_max_jobs_per_minute,
            "max_write_queue_depth": settings.import_max_write_queue_depth,
            "low_priority": settings.import_low_priority,
            "reconstruction_default": settings.import_reconstruction_default,
            "reconstruction_worker_enabled": settings.import_reconstruction_worker_enabled,
            "reconstruction_concurrency": settings.import_reconstruction_concurrency,
            "reconstruction_worker_batch_size": settings.import_reconstruction_worker_batch_size,
            "reconstruction_lease_seconds": settings.import_reconstruction_lease_seconds,
            "reconstruction_max_attempts": settings.import_reconstruction_max_attempts,
            "reconstruction_retry_base_seconds": settings.import_reconstruction_retry_base_seconds,
            "reconstruction_poll_seconds": settings.import_reconstruction_poll_seconds,
        },
        "scheduler": {
            "backend": settings.hot_scheduler,
            "max_consecutive_low_priority": settings.scheduler_max_consecutive_low_priority,
            "hot_lane_target_wait_ms": settings.hot_lane_target_wait_ms,
            "preflight_persist_max_wait_ms": settings.preflight_persist_max_wait_ms,
        },
    }
    return redact_secrets(config)
