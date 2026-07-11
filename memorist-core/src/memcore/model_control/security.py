from __future__ import annotations

import re
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
    return any(
        _is_secret_fragment_key(key)
        for key, _value in parse_qsl(parts.query, keep_blank_values=True)
    )


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
                (key, REDACTED_VALUE if _is_secret_fragment_key(key) else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
    else:
        query = ""
    return parts._replace(netloc=netloc, query=query).geturl()


_SECRET_FRAGMENT_KEYS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
)
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|secret(?:_env_var_name)?|password|credential|authorization)"
    r"(\s*[:=]\s*)"
    r"(?!\[redacted\]|%5Bredacted%5D)"
    r"([^\s,;&}\]\)]+)"
)
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bbearer\s+(?!\[redacted\])[^\s,;&}\]\)]+")
_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


def _is_secret_fragment_key(key: str) -> bool:
    normalized_key = key.lower().replace("-", "_")
    return is_secret_key(normalized_key) or any(
        secret_key in normalized_key for secret_key in _SECRET_FRAGMENT_KEYS
    )


def _redact_url_match(match: re.Match[str]) -> str:
    return redact_endpoint(match.group(0)) or REDACTED_VALUE


def sanitize_error_message(message: str | None) -> str | None:
    if message is None:
        return None
    sanitized = _URL_PATTERN.sub(_redact_url_match, message)
    sanitized = _BEARER_TOKEN_PATTERN.sub("Bearer [redacted]", sanitized)
    sanitized = _KEY_VALUE_SECRET_PATTERN.sub(r"\1\2[redacted]", sanitized)
    return sanitized[:500]
