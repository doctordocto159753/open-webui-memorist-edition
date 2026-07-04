from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
    OpenAICompatibleProviderConfig,
)
from tests.support.fake_openai_provider import FakeOpenAIHandler


def test_openai_compatible_provider_reads_structured_json() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenAICompatibleMemoryExtractionProvider(
            OpenAICompatibleProviderConfig(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="fake",
                supports_json_mode=True,
            )
        )
        response = provider.extract(
            system_prompt="Return JSON",
            input_payload={"sentences": [{"text": "Remember to use Jira."}]},
        )
        assert response.output["prompt_id"] == "memorist.jakobson_sentence_analysis"
        assert response.output["sentence_count"] == 1
        assert response.input_tokens == 42
    finally:
        server.shutdown()
