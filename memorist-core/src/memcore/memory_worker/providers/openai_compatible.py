from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from memcore.memory_worker.providers.base import ProviderResponse


class OpenAICompatibleProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAICompatibleProviderConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_ms: int = 8000
    supports_structured_output: bool = False
    supports_json_mode: bool = True


class OpenAICompatibleMemoryExtractionProvider:
    def __init__(self, config: OpenAICompatibleProviderConfig) -> None:
        self.config = config

    @classmethod
    def from_profile(
        cls, profile: dict[str, Any], *, timeout_ms: int = 8000
    ) -> OpenAICompatibleMemoryExtractionProvider:
        secret_env = profile.get("secret_env_var_name")
        api_key = os.environ.get(str(secret_env)) if secret_env else None
        endpoint = str(profile.get("endpoint_url") or "").rstrip("/")
        if not endpoint:
            raise OpenAICompatibleProviderError("model profile endpoint_url is required")
        return cls(
            OpenAICompatibleProviderConfig(
                base_url=endpoint,
                model=str(profile.get("model_name") or "unknown"),
                api_key=api_key,
                timeout_ms=timeout_ms,
                supports_structured_output=bool(profile.get("supports_structured_output")),
                supports_json_mode=bool(profile.get("supports_json_mode", True)),
            )
        )

    def extract(self, *, system_prompt: str, input_payload: dict[str, Any]) -> ProviderResponse:
        started = time.perf_counter()
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(input_payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": 0,
        }
        if self.config.supports_json_mode or self.config.supports_structured_output:
            body["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            _completion_url(self.config.base_url),
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_ms / 1000) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OpenAICompatibleProviderError(_sanitize_error_message(str(error))) from error
        try:
            message = payload["choices"][0]["message"]["content"]
            output = json.loads(message) if isinstance(message, str) else message
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise OpenAICompatibleProviderError(
                "provider response did not contain structured JSON content"
            ) from error
        if not isinstance(output, dict):
            raise OpenAICompatibleProviderError("provider structured output must be a JSON object")
        usage = payload.get("usage") if isinstance(payload, dict) else {}
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProviderResponse(
            output=output,
            input_tokens=int((usage or {}).get("prompt_tokens") or 0),
            output_tokens=int((usage or {}).get("completion_tokens") or 0),
            latency_ms=latency_ms,
            provider_response_id=str(payload.get("id")) if payload.get("id") else None,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers


def _sanitize_error_message(message: str) -> str:
    sanitized = message
    for marker in ("Authorization", "Bearer", "api_key", "token", "secret", "password"):
        sanitized = sanitized.replace(marker, "[redacted]")
    return sanitized[:500]


def _completion_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"
