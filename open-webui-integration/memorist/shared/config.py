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
    preflight_timeout_ms: int = 1200
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


def load_config() -> MemoristIntegrationConfig:
    return MemoristIntegrationConfig(
        core_url=os.getenv("MEMORIST_CORE_URL", "http://localhost:8777"),
        enabled=_bool_env("MEMORIST_ENABLED", True),
        preflight_enabled=_bool_env("MEMORIST_PREFLIGHT_ENABLED", True),
        preflight_timeout_ms=_int_env("MEMORIST_PREFLIGHT_TIMEOUT_MS", 1200),
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
