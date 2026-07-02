from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from memcore.config import REDACTED_VALUE, is_secret_key
from memcore.validators.ijson import load_ijson


class ModelControlSecurityError(ValueError):
    """Raised when model-control input violates local-first secret policy."""


def reject_raw_secrets(value: Any, path: str = "payload") -> None:
    if value is None:
        return
    if isinstance(value, str):
        try:
            parsed = load_ijson(value)
        except ValueError:
            return
        reject_raw_secrets(parsed, path)
        return
    if isinstance(value, Mapping):
        for key, child_value in value.items():
            key_text = str(key)
            if is_secret_key(key_text):
                raise ModelControlSecurityError(f"{path}.{key_text} must not contain raw secrets")
            reject_raw_secrets(child_value, f"{path}.{key_text}")
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        for index, child_value in enumerate(value):
            reject_raw_secrets(child_value, f"{path}[{index}]")


def validate_secret_strategy(secret_strategy: str, secret_env_var_name: str | None) -> None:
    forbidden = {"raw_secret_in_sqlite", "raw_secret_in_postgres"}
    allowed = {"none", "environment_reference", "openwebui_managed_future", "env_var"}
    if secret_strategy in forbidden:
        raise ModelControlSecurityError(f"{secret_strategy} is forbidden")
    if secret_strategy not in allowed:
        raise ModelControlSecurityError("unsupported secret strategy")
    if secret_strategy in {"env_var", "environment_reference"} and not secret_env_var_name:
        raise ModelControlSecurityError(
            "secret_env_var_name is required for environment reference strategy"
        )
    if secret_env_var_name is not None and not secret_env_var_name.isidentifier():
        raise ModelControlSecurityError("secret_env_var_name must be an environment variable name")


def endpoint_contains_secret(endpoint_url: str | None) -> bool:
    if not endpoint_url:
        return False
    parts = urlsplit(endpoint_url)
    if parts.username or parts.password:
        return True
    return any(is_secret_key(key) for key, _value in parse_qsl(parts.query, keep_blank_values=True))


def endpoint_is_local(endpoint_url: str | None) -> bool:
    if not endpoint_url:
        return True
    host = (urlsplit(endpoint_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".local")


def redact_endpoint(endpoint_url: str | None) -> str | None:
    if endpoint_url is None:
        return None
    parts = urlsplit(endpoint_url)
    hostname = parts.hostname or ""
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    if parts.query:
        query = urlencode(
            [
                (key, REDACTED_VALUE if is_secret_key(key) else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
    else:
        query = ""
    return parts._replace(netloc=netloc, query=query).geturl()


def sanitize_error_message(message: str | None) -> str | None:
    if message is None:
        return None
    sanitized = message
    for marker in ("Authorization", "Bearer", "api_key", "token", "secret", "password"):
        sanitized = sanitized.replace(marker, "[redacted]")
    return sanitized[:500]
