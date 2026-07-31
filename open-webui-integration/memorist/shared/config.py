from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class MemoristIntegrationConfig:
    core_url: str = "http://localhost:8777"
    enabled: bool = True
    preflight_enabled: bool = True
    preflight_timeout_ms: int = 60_000
    capture_timeout_ms: int = 2500
    control_timeout_ms: int = 15_000
    provider_test_timeout_ms: int = 60_000
    admin_timeout_ms: int = 15_000
    import_timeout_ms: int = 300_000
    attachment_token_budget: int = 1800
    attachment_max_tokens: int = 1800
    retrieval_mode: str = "standard"
    fail_open: bool = True
    debug: bool = False
    actor_assertion_secret: str | None = None
    actor_service_token: str | None = None
    actor_assertion_issuer: str = "openwebui-backend"
    actor_assertion_audience: str = "memorist-core"

    @property
    def timeout_seconds(self) -> float:
        return max(0.001, self.preflight_timeout_ms / 1000)

    def timeout_seconds_for(self, path: str) -> float:
        if path == "/memcore/preflight":
            timeout_ms = self.preflight_timeout_ms
        elif "/model-control/profiles/" in path and path.endswith("/test"):
            timeout_ms = self.provider_test_timeout_ms
        elif path.startswith("/memcore/model-control/"):
            timeout_ms = self.control_timeout_ms
        elif path.startswith("/memcore/imports"):
            timeout_ms = self.import_timeout_ms
        elif path in {"/memcore/health", "/memcore/openwebui/status"}:
            timeout_ms = self.admin_timeout_ms
        else:
            timeout_ms = self.capture_timeout_ms
        return max(0.001, timeout_ms / 1000)


def load_config() -> MemoristIntegrationConfig:
    return MemoristIntegrationConfig(
        core_url=os.getenv("MEMORIST_CORE_URL", "http://localhost:8777"),
        enabled=_bool_env("MEMORIST_ENABLED", True),
        preflight_enabled=_bool_env("MEMORIST_PREFLIGHT_ENABLED", True),
        preflight_timeout_ms=_int_env("MEMORIST_PREFLIGHT_TIMEOUT_MS", 60_000),
        capture_timeout_ms=_int_env("MEMORIST_CAPTURE_TIMEOUT_MS", 2500),
        control_timeout_ms=_int_env("MEMORIST_CONTROL_TIMEOUT_MS", 15_000),
        provider_test_timeout_ms=_int_env("MEMORIST_PROVIDER_TEST_TIMEOUT_MS", 60_000),
        admin_timeout_ms=_int_env("MEMORIST_ADMIN_TIMEOUT_MS", 15_000),
        import_timeout_ms=_int_env("MEMORIST_IMPORT_TIMEOUT_MS", 300_000),
        attachment_token_budget=_int_env("MEMORIST_ATTACHMENT_TOKEN_BUDGET", 1800),
        attachment_max_tokens=_int_env("MEMORIST_ATTACHMENT_MAX_TOKENS", 1800),
        retrieval_mode=os.getenv("MEMORIST_RETRIEVAL_MODE", "standard"),
        fail_open=_bool_env("MEMORIST_FAIL_OPEN", True),
        debug=_bool_env("MEMORIST_DEBUG", False),
        actor_assertion_secret=os.getenv("MEMORIST_ACTOR_ASSERTION_SECRET"),
        actor_service_token=os.getenv("MEMORIST_ACTOR_SERVICE_TOKEN"),
        actor_assertion_issuer=os.getenv(
            "MEMORIST_ACTOR_ASSERTION_ISSUER", "openwebui-backend"
        ),
        actor_assertion_audience=os.getenv(
            "MEMORIST_ACTOR_ASSERTION_AUDIENCE", "memorist-core"
        ),
    )
