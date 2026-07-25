from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Sized
from time import perf_counter
from typing import cast

from memcore.model_control.endpoint import openai_operation_url
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
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.model_name = model_name
        self.secret_env_var_name = secret_env_var_name
        self.supports_json_mode = supports_json_mode
        self.supports_structured_output = supports_structured_output
        self.requires_structured_extraction = requires_structured_extraction

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth:
        started = perf_counter()
        deadline = started + timeout_seconds
        schema_mode = _schema_mode(self)
        payload = _health_probe_payload(self.model_name, schema_mode=schema_mode)

        try:
            response_status, content = self._send_health_probe(
                payload,
                timeout_seconds=_remaining_timeout(deadline),
            )
            if content is None:
                return _connected_health(
                    self,
                    started,
                    response_status,
                    overall_status="incompatible",
                    structured_output_status="invalid_response",
                    role_compatibility_status="incompatible",
                    detail="Missing choices[0].message.content",
                    recommended_action="Select a chat-completions compatible model.",
                    failure_stage="response_shape",
                    schema_mode=schema_mode,
                )
            marker = _parse_json_content(content)
            if marker is None:
                return _connected_health(
                    self,
                    started,
                    response_status,
                    overall_status="incompatible",
                    structured_output_status="malformed",
                    role_compatibility_status="incompatible",
                    detail="Malformed JSON in chat completion response.",
                    recommended_action=(
                        "Enable JSON mode or select a model with reliable structured output."
                    ),
                    failure_stage="json_decode",
                    schema_mode=schema_mode,
                )
            attempts = 1
            if marker.get("memorist_provider_test") != "ok":
                attempts = 2
                corrective = _health_probe_payload(
                    self.model_name,
                    schema_mode=schema_mode,
                    corrective=True,
                )
                response_status, corrected_content = self._send_health_probe(
                    corrective,
                    timeout_seconds=_remaining_timeout(deadline),
                )
                corrected = (
                    _parse_json_content(corrected_content)
                    if corrected_content is not None
                    else None
                )
                if corrected is None or corrected.get("memorist_provider_test") != "ok":
                    return _connected_health(
                        self,
                        started,
                        response_status,
                        overall_status="incompatible",
                        structured_output_status=(
                            "valid_json_wrong_marker"
                            if corrected is not None
                            else "invalid_response"
                        ),
                        role_compatibility_status="incompatible",
                        detail=(
                            "Provider returned JSON but did not satisfy the exact "
                            "memorist_provider_test marker after one corrective retry."
                        ),
                        recommended_action=(
                            "Select a model that follows exact JSON field and value constraints."
                        ),
                        failure_stage="semantic_marker",
                        schema_mode=schema_mode,
                        probe_attempts=attempts,
                    )
            return _connected_health(
                self,
                started,
                response_status,
                overall_status="ok",
                structured_output_status="supported",
                role_compatibility_status="compatible",
                detail=_health_detail_for_success(
                    response_status,
                    supports_json_format=schema_mode != "none",
                    requires_structured_extraction=self.requires_structured_extraction,
                ),
                recommended_action="No action required.",
                schema_mode=schema_mode,
                probe_attempts=attempts,
            )
        except json.JSONDecodeError as error:
            return _connected_health(
                self,
                started,
                200,
                overall_status="incompatible",
                structured_output_status="malformed",
                role_compatibility_status="incompatible",
                detail=f"Malformed provider response: {sanitize_error_message(str(error))}",
                recommended_action="Check the endpoint and select an OpenAI-compatible model.",
            )
        except urllib.error.HTTPError as error:
            error_detail = _read_http_error_detail(error)
            return _http_error_health(
                self,
                started,
                error,
                error_detail,
                response_format_rejected=(
                    schema_mode != "none" and _looks_like_response_format_rejection(error_detail)
                ),
                schema_mode=schema_mode,
            )
        except MissingSecretEnvironmentVariableError as error:
            return _configuration_health(self, started, str(error))
        except TimeoutError as error:
            return _transport_health(self, started, error, timeout=True)
        except urllib.error.URLError as error:
            return _transport_health(
                self,
                started,
                error,
                timeout=isinstance(error.reason, (TimeoutError, socket.timeout)),
            )
        except OSError as error:
            return _transport_health(self, started, error, timeout=False)

    def _send_health_probe(
        self,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> tuple[int, str | None]:
        request = urllib.request.Request(
            _openai_url(self.endpoint_url, "chat/completions"),
            data=json.dumps(payload).encode("utf-8"),
            headers=_headers(self.secret_env_var_name),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
            return int(response.status), _extract_chat_content(data)

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

    def __init__(
        self,
        endpoint_url: str,
        model_name: str,
        secret_env_var_name: str | None = None,
        supports_json_mode: bool = False,
        supports_structured_output: bool = False,
        requires_structured_extraction: bool = False,
        embedding_dimension: int | None = None,
    ) -> None:
        super().__init__(
            endpoint_url,
            model_name,
            secret_env_var_name,
            supports_json_mode=supports_json_mode,
            supports_structured_output=supports_structured_output,
            requires_structured_extraction=requires_structured_extraction,
        )
        self.embedding_dimension = embedding_dimension

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth:
        started = perf_counter()
        payload = {"model": self.model_name, "input": ["Memorist embedding connectivity test."]}
        try:
            request = urllib.request.Request(
                _openai_url(self.endpoint_url, "embeddings"),
                data=json.dumps(payload).encode("utf-8"),
                headers=_headers(self.secret_env_var_name),
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
                embedding = _extract_first_embedding(data)
                if embedding is None:
                    return _connected_health(
                        self,
                        started,
                        response.status,
                        overall_status="incompatible",
                        structured_output_status="not_applicable",
                        role_compatibility_status="incompatible",
                        detail="Missing data[0].embedding in embeddings response",
                        recommended_action="Select an embeddings-compatible model.",
                        chat_completion_status="not_applicable",
                    )
                elif not _is_non_empty_numeric_vector(embedding):
                    return _connected_health(
                        self,
                        started,
                        response.status,
                        overall_status="incompatible",
                        structured_output_status="not_applicable",
                        role_compatibility_status="incompatible",
                        detail=(
                            "Embedding response data[0].embedding must be a non-empty numeric "
                            "vector"
                        ),
                        recommended_action="Select a compatible embedding model.",
                        chat_completion_status="not_applicable",
                    )
                else:
                    dimension = len(cast(Sized, embedding))
                    if (
                        self.embedding_dimension is not None
                        and dimension != self.embedding_dimension
                    ):
                        return _connected_health(
                            self,
                            started,
                            response.status,
                            overall_status="incompatible",
                            structured_output_status="not_applicable",
                            role_compatibility_status="incompatible",
                            detail=(
                                "Embedding dimension mismatch: profile expects "
                                f"{self.embedding_dimension}, provider returned {dimension}. "
                                "Update the profile embedding_dimension or choose a "
                                "compatible model."
                            ),
                            recommended_action=(
                                "Update embedding_dimension or choose a matching model."
                            ),
                            chat_completion_status="not_applicable",
                        )
                    return _connected_health(
                        self,
                        started,
                        response.status,
                        overall_status="ok",
                        structured_output_status="not_applicable",
                        role_compatibility_status="compatible",
                        detail=(
                            f"HTTP {response.status}; embeddings validated; dimension={dimension}"
                        ),
                        recommended_action="No action required.",
                        chat_completion_status="not_applicable",
                    )
        except json.JSONDecodeError as error:
            return _connected_health(
                self,
                started,
                200,
                overall_status="incompatible",
                structured_output_status="not_applicable",
                role_compatibility_status="incompatible",
                detail=f"Malformed embeddings response: {sanitize_error_message(str(error))}",
                recommended_action="Check the endpoint and embedding model.",
                chat_completion_status="not_applicable",
            )
        except urllib.error.HTTPError as error:
            return _http_error_health(
                self,
                started,
                error,
                _read_http_error_detail(error),
                response_format_rejected=False,
                chat_completion_status="not_applicable",
            )
        except MissingSecretEnvironmentVariableError as error:
            return _configuration_health(self, started, str(error))
        except TimeoutError as error:
            return _transport_health(self, started, error, timeout=True)
        except urllib.error.URLError as error:
            return _transport_health(
                self,
                started,
                error,
                timeout=isinstance(error.reason, (TimeoutError, socket.timeout)),
            )
        except OSError as error:
            return _transport_health(self, started, error, timeout=False)

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
            dns_or_host_reachable="reachable" if status == "ok" else "unknown",
            tcp_or_http_reachable="reachable" if status == "ok" else "unreachable",
            authentication_status="not_applicable",
            model_status="available" if status == "ok" else "unknown",
            chat_completion_status="not_tested",
            structured_output_status="not_tested",
            role_compatibility_status="connected_only" if status == "ok" else "unknown",
            overall_status="connected_with_warning" if status == "ok" else "unreachable",
            detail_sanitized=detail,
            recommended_action=(
                "Run a role-specific model test."
                if status == "ok"
                else "Check the Ollama endpoint and service."
            ),
        )


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
    content = message.get("content")
    return content if isinstance(content, str) and content else None


def _parse_json_content(content: str) -> dict[str, object] | None:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _extract_first_embedding(data: object) -> object | None:
    if not isinstance(data, dict):
        return None
    records = data.get("data")
    if not isinstance(records, list) or not records:
        return None
    first_record = records[0]
    if not isinstance(first_record, dict):
        return None
    return first_record.get("embedding")


def _is_non_empty_numeric_vector(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, int | float) and not isinstance(item, bool) for item in value)
    )


class MissingSecretEnvironmentVariableError(RuntimeError):
    def __init__(self, secret_env_var_name: str) -> None:
        super().__init__(f"Secret environment variable {secret_env_var_name} is not set")


def _headers(secret_env_var_name: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if secret_env_var_name:
        secret = os.environ.get(secret_env_var_name)
        if not secret:
            raise MissingSecretEnvironmentVariableError(secret_env_var_name)
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _openai_url(endpoint_url: str, path: str) -> str:
    return openai_operation_url(endpoint_url, path)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _connected_health(
    provider: OpenAICompatibleLLMProvider,
    started: float,
    http_status: int,
    *,
    overall_status: str,
    structured_output_status: str,
    role_compatibility_status: str,
    detail: str,
    recommended_action: str,
    chat_completion_status: str = "supported",
    failure_stage: str | None = None,
    schema_mode: str = "none",
    probe_attempts: int = 1,
) -> ProviderHealth:
    return ProviderHealth(
        status="ok" if overall_status in {"ok", "connected_with_warning"} else "error",
        provider_type=provider.provider_type,
        model_name=provider.model_name,
        latency_ms=_elapsed_ms(started),
        local_only_safe=endpoint_is_local(provider.endpoint_url),
        dns_or_host_reachable="reachable",
        tcp_or_http_reachable="reachable",
        authentication_status="valid",
        model_status="available",
        chat_completion_status=chat_completion_status,
        structured_output_status=structured_output_status,
        role_compatibility_status=role_compatibility_status,
        overall_status=overall_status,
        http_status=http_status,
        detail_sanitized=sanitize_error_message(detail),
        recommended_action=recommended_action,
        failure_stage=failure_stage,
        schema_mode=schema_mode,
        probe_attempts=probe_attempts,
    )


def _http_error_health(
    provider: OpenAICompatibleLLMProvider,
    started: float,
    error: urllib.error.HTTPError,
    detail: str,
    *,
    response_format_rejected: bool,
    chat_completion_status: str = "failed",
    schema_mode: str = "none",
) -> ProviderHealth:
    code = int(error.code)
    authentication = "invalid" if code in {401, 403} else "valid"
    model_status = "not_found" if code == 404 else "unknown"
    overall = "unknown_error"
    retryable = code == 429 or code >= 500
    rate_limited = code == 429
    role_status = "unknown"
    structured_status = "unknown"
    action = "Review the sanitized provider response and retry."
    safe_detail = detail
    if code in {401, 403}:
        overall = "authentication_failed"
        action = "Check the API-key environment-variable reference and provider permissions."
    elif code == 404:
        overall = "incompatible"
        role_status = "incompatible"
        action = "Check the model ID and the canonical /v1 provider endpoint."
    elif code == 429:
        overall = "rate_limited"
        role_status = "temporarily_unavailable"
        action = "Wait and retry, or check provider quota and rate limits."
    elif response_format_rejected:
        overall = "incompatible"
        structured_status = "unsupported"
        role_status = "incompatible"
        safe_detail = (
            "Provider rejected JSON response_format; disable Supports JSON mode or "
            "choose a compatible model."
        )
        action = "Disable JSON mode or select a structured-output compatible model."
    elif code >= 500:
        overall = "unknown_error"
        role_status = "temporarily_unavailable"
        action = "Retry later; the provider returned a server error."
    return ProviderHealth(
        status="error",
        provider_type=provider.provider_type,
        model_name=provider.model_name,
        latency_ms=_elapsed_ms(started),
        local_only_safe=endpoint_is_local(provider.endpoint_url),
        dns_or_host_reachable="reachable",
        tcp_or_http_reachable="reachable",
        authentication_status=authentication,
        model_status=model_status,
        chat_completion_status=chat_completion_status,
        structured_output_status=structured_status,
        role_compatibility_status=role_status,
        overall_status=overall,
        http_status=code,
        retryable=retryable,
        quota_or_rate_limited=rate_limited,
        detail_sanitized=sanitize_error_message(safe_detail),
        recommended_action=action,
        failure_stage="http",
        schema_mode=schema_mode,
    )


def _configuration_health(
    provider: OpenAICompatibleLLMProvider,
    started: float,
    detail: str,
) -> ProviderHealth:
    return ProviderHealth(
        status="error",
        provider_type=provider.provider_type,
        model_name=provider.model_name,
        latency_ms=_elapsed_ms(started),
        local_only_safe=endpoint_is_local(provider.endpoint_url),
        authentication_status="missing_secret_reference",
        model_status="not_tested",
        chat_completion_status="not_tested",
        structured_output_status="not_tested",
        role_compatibility_status="not_tested",
        overall_status="misconfigured",
        detail_sanitized=sanitize_error_message(detail),
        recommended_action="Set the named environment variable in the Memorist Core runtime.",
        failure_stage="configuration",
    )


def _transport_health(
    provider: OpenAICompatibleLLMProvider,
    started: float,
    error: BaseException,
    *,
    timeout: bool,
) -> ProviderHealth:
    overall = "timeout" if timeout else "unreachable"
    return ProviderHealth(
        status="error",
        provider_type=provider.provider_type,
        model_name=provider.model_name,
        latency_ms=_elapsed_ms(started),
        local_only_safe=endpoint_is_local(provider.endpoint_url),
        dns_or_host_reachable="unknown" if timeout else "unreachable",
        tcp_or_http_reachable="timeout" if timeout else "unreachable",
        authentication_status="not_tested",
        model_status="not_tested",
        chat_completion_status="not_tested",
        structured_output_status="not_tested",
        role_compatibility_status="not_tested",
        overall_status=overall,
        retryable=True,
        detail_sanitized=sanitize_error_message(str(error)),
        recommended_action=(
            "Increase the configurable test timeout or check provider latency."
            if timeout
            else "Check DNS, container routing, port, and the provider base URL."
        ),
        failure_stage="timeout" if timeout else "transport",
    )


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


def _read_http_error_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read(4096).decode("utf-8", errors="replace")
    except OSError:
        body = ""
    detail = f"HTTP {error.code}: {body}" if body else f"HTTP {error.code}: {error.reason}"
    return sanitize_error_message(detail) or f"HTTP {error.code}"


def _looks_like_response_format_rejection(detail: str) -> bool:
    lowered = detail.lower()
    return "response_format" in lowered or (
        "json" in lowered and any(term in lowered for term in ("unsupported", "reject", "invalid"))
    )


def _schema_mode(provider: OpenAICompatibleLLMProvider) -> str:
    if provider.supports_structured_output:
        return "json_schema"
    if provider.supports_json_mode:
        return "json_object"
    return "none"


def _health_probe_payload(
    model_name: str,
    *,
    schema_mode: str,
    corrective: bool = False,
) -> dict[str, object]:
    instruction = (
        "Correct the previous response. Return exactly one JSON object with exactly "
        'one field named "memorist_provider_test" whose string value is "ok".'
        if corrective
        else "Return exactly one JSON object with exactly one field named "
        '"memorist_provider_test" whose string value is exactly "ok". '
        "Do not add fields, prose, or markdown."
    )
    payload: dict[str, object] = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "This is a Memorist provider compatibility test. Follow the "
                    "requested JSON contract exactly."
                ),
            },
            {"role": "user", "content": instruction},
        ],
    }
    if schema_mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "memorist_provider_health",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"memorist_provider_test": {"type": "string", "const": "ok"}},
                    "required": ["memorist_provider_test"],
                    "additionalProperties": False,
                },
            },
        }
    elif schema_mode == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - perf_counter()
    if remaining <= 0:
        raise TimeoutError("provider compatibility test exceeded its total timeout")
    return max(0.001, remaining)
