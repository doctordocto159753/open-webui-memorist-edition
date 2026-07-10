from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from time import perf_counter
from urllib.parse import urlsplit

from memcore.model_control.providers.base import (
    EmbeddingResponse,
    ProviderHealth,
    StructuredCompletionResponse,
    TokenEstimate,
    heuristic_token_count,
)
from memcore.model_control.security import endpoint_is_local, sanitize_error_message


class OpenAICompatibleLLMProvider:
    provider_type = "openai_compatible_llm"

    def __init__(
        self,
        endpoint_url: str,
        model_name: str,
        secret_env_var_name: str | None = None,
        supports_json_mode: bool = False,
        supports_structured_output: bool = False,
        requires_structured_extraction: bool = False,
        supports_structured_output: bool = False,
        supports_json_mode: bool = False,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.model_name = model_name
        self.secret_env_var_name = secret_env_var_name
        self.supports_json_mode = supports_json_mode
        self.supports_structured_output = supports_structured_output
        self.requires_structured_extraction = requires_structured_extraction
        self.supports_structured_output = supports_structured_output
        self.supports_json_mode = supports_json_mode

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth:
        started = perf_counter()
        status = "error"
        detail: str | None = None
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": 'Return exactly {"memorist_provider_test":"ok"} as JSON.',
                }
            ],
            "max_tokens": 16,
        }
        supports_json_format = self.supports_json_mode or self.supports_structured_output
        if supports_json_format:
            payload["response_format"] = {"type": "json_object"}
        detail: str | None
        payload: dict[str, object] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "Memorist connectivity test. Reply only with valid JSON.",
                },
                {"role": "user", "content": '{"memorist_provider_test":"ok"}'},
            ],
        }
        if self.supports_json_mode or self.supports_structured_output:
            payload["response_format"] = {"type": "json_object"}

        try:
            request = urllib.request.Request(
                _openai_url(self.endpoint_url, "chat/completions"),
                data=json.dumps(payload).encode("utf-8"),
                headers=_headers(self.secret_env_var_name),
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
                data = json.loads(response_body)
                if not 200 <= response.status < 300:
                    detail = f"HTTP {response.status}"
                else:
                    content = _extract_chat_content(data)
                    if content is None:
                        detail = "Missing choices[0].message.content"
                    else:
                        marker = json.loads(content) if isinstance(content, str) else content
                        if (
                            isinstance(marker, dict)
                            and marker.get("memorist_provider_test") == "ok"
                        ):
                            status = "ok"
                            detail = _health_detail_for_success(
                                response.status,
                                supports_json_format=bool(supports_json_format),
                                requires_structured_extraction=self.requires_structured_extraction,
                            )
                        marker = json.loads(content)
                        if marker.get("memorist_provider_test") == "ok":
                            status = "ok"
                            detail = f"HTTP {response.status}; chat completions validated"
                        else:
                            detail = "Provider health marker mismatch"
        except json.JSONDecodeError as error:
            detail = f"Malformed JSON response: {sanitize_error_message(str(error))}"
        except urllib.error.HTTPError as error:
            error_detail = _read_http_error_detail(error)
            if supports_json_format and _looks_like_response_format_rejection(error_detail):
                detail = (
                    "Provider rejected JSON response_format; disable Supports JSON mode or "
                    "choose a compatible model."
                )
            else:
                detail = sanitize_error_message(
                    error_detail or f"HTTP {error.code}: {error.reason}"
                )
            detail = sanitize_error_message(f"HTTP {error.code}: {error.reason}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            detail = sanitize_error_message(str(error))
        return ProviderHealth(
            status=status,
            provider_type=self.provider_type,
            model_name=self.model_name,
            latency_ms=_elapsed_ms(started),
            local_only_safe=endpoint_is_local(self.endpoint_url),
            detail=detail,
        )

    def estimate_tokens(self, input_text: str = "", output_text: str = "") -> TokenEstimate:
        return TokenEstimate(
            input_tokens=heuristic_token_count(input_text),
            output_tokens=heuristic_token_count(output_text),
            estimated_cost=0.0,
        )

    def complete_structured(
        self,
        prompt: str,
        timeout_seconds: float = 1.0,
    ) -> StructuredCompletionResponse:
        started = perf_counter()
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            _openai_url(self.endpoint_url, "chat/completions"),
            data=json.dumps(payload).encode("utf-8"),
            headers=_headers(self.secret_env_var_name),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            if isinstance(data, dict)
            else "{}"
        )
        output = json.loads(str(content))
        return StructuredCompletionResponse(
            output_ijson=output,
            input_tokens=heuristic_token_count(prompt),
            output_tokens=heuristic_token_count(str(content)),
            latency_ms=_elapsed_ms(started),
        )

    def embed(self, texts: list[str], timeout_seconds: float = 1.0) -> EmbeddingResponse:
        raise RuntimeError("LLM provider does not implement embeddings")


class OpenAICompatibleEmbeddingProvider(OpenAICompatibleLLMProvider):
    provider_type = "openai_compatible_embedding"

    def embed(self, texts: list[str], timeout_seconds: float = 1.0) -> EmbeddingResponse:
        started = perf_counter()
        payload = {"model": self.model_name, "input": texts}
        request = urllib.request.Request(
            _openai_url(self.endpoint_url, "embeddings"),
            data=json.dumps(payload).encode("utf-8"),
            headers=_headers(self.secret_env_var_name),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        records = data.get("data", []) if isinstance(data, dict) else []
        vectors = [record.get("embedding", []) for record in records if isinstance(record, dict)]
        return EmbeddingResponse(
            vectors=[[float(value) for value in vector] for vector in vectors],
            input_tokens=sum(heuristic_token_count(text) for text in texts),
            latency_ms=_elapsed_ms(started),
        )


class OllamaProvider(OpenAICompatibleLLMProvider):
    provider_type = "ollama"

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth:
        started = perf_counter()
        detail: str | None
        try:
            request = urllib.request.Request(
                f"{self.endpoint_url}/api/tags",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = "ok" if 200 <= response.status < 300 else "error"
                detail = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            status = "error"
            detail = sanitize_error_message(str(error))
        return ProviderHealth(
            status=status,
            provider_type=self.provider_type,
            model_name=self.model_name,
            latency_ms=_elapsed_ms(started),
            local_only_safe=endpoint_is_local(self.endpoint_url),
            detail=detail,
        )


def _extract_chat_content(data: object) -> object | None:
def _extract_chat_content(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None
    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None
    return message.get("content")
    content = message.get("content")
    return content if isinstance(content, str) and content else None


def _headers(secret_env_var_name: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if secret_env_var_name:
        secret = os.environ.get(secret_env_var_name)
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
    return headers


def _openai_url(endpoint_url: str, path: str) -> str:
    base = endpoint_url.rstrip("/")
    versioned_base = base if urlsplit(base).path.endswith("/v1") else f"{base}/v1"
    return f"{versioned_base}/{path.lstrip('/')}"


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _health_detail_for_success(
    http_status: int,
    *,
    supports_json_format: bool,
    requires_structured_extraction: bool,
) -> str:
    if requires_structured_extraction and not supports_json_format:
        return (
            f"HTTP {http_status}; chat completions validated; warning: this profile does not "
            "declare Supports JSON mode or Supports structured output and may be unsuitable "
            "for structured memory tasks. Enable one of those capabilities or choose a "
            "compatible model."
        )
    return f"HTTP {http_status}; chat completions validated"
            f"HTTP {http_status}; warning: this profile does not declare Supports JSON mode or "
            "Supports structured output and may be unsuitable for structured memory tasks. "
            "Enable one of those capabilities or choose a compatible model."
        )
    return f"HTTP {http_status}"


def _read_http_error_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
    except OSError:
        body = ""
    return f"HTTP {error.code}: {body}" if body else f"HTTP {error.code}: {error.reason}"


def _looks_like_response_format_rejection(detail: str) -> bool:
    lowered = detail.lower()
    return "response_format" in lowered or (
        "json" in lowered and any(term in lowered for term in ("unsupported", "reject", "invalid"))
    )
