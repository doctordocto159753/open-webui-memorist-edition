from __future__ import annotations

import json
import urllib.request
from time import perf_counter
from typing import Any

from memcore.model_control.providers.base import StructuredCompletionResponse, heuristic_token_count
from memcore.model_control.providers.openai_compatible import (
    OpenAICompatibleLLMProvider,
    _elapsed_ms,
    _headers,
    _openai_url,
)
from memcore.model_control.runtime_contracts import (
    json_mode_contract_suffix,
    runtime_contract_from_prompt,
)


class RuntimeContractOpenAICompatibleLLMProvider(OpenAICompatibleLLMProvider):
    """OpenAI-compatible provider that binds StageInvoker calls to role schemas.

    Generic prompt-registry calls keep their existing JSON-object behavior. A
    StageInvoker prompt carries a ``ROLE=`` line; for those calls the same
    authoritative runtime contract used by certification determines the strict
    JSON Schema request or the schema-bearing JSON-mode prompt.
    """

    def complete_structured(
        self,
        prompt: str,
        timeout_seconds: float = 1.0,
    ) -> StructuredCompletionResponse:
        started = perf_counter()
        runtime_contract = runtime_contract_from_prompt(prompt)
        effective_prompt = prompt
        response_format: dict[str, Any]
        if runtime_contract is not None and self.supports_structured_output:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": runtime_contract.schema_name,
                    "strict": True,
                    "schema": runtime_contract.json_schema,
                },
            }
        elif self.supports_json_mode or self.supports_structured_output:
            response_format = {"type": "json_object"}
            if runtime_contract is not None:
                effective_prompt += json_mode_contract_suffix(runtime_contract)
        elif runtime_contract is not None or self.requires_structured_extraction:
            raise RuntimeError(
                "structured processing requires Supports structured output or Supports JSON mode"
            )
        else:
            response_format = {"type": "json_object"}

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": effective_prompt}],
            "response_format": response_format,
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
        if not isinstance(output, dict):
            raise ValueError("structured provider response must be a JSON object")
        if runtime_contract is not None:
            runtime_contract.validate(output)
        return StructuredCompletionResponse(
            output_ijson=output,
            input_tokens=heuristic_token_count(effective_prompt),
            output_tokens=heuristic_token_count(str(content)),
            latency_ms=_elapsed_ms(started),
        )
