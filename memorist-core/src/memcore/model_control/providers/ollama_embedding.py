from memcore.model_control.providers.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
)


class OllamaEmbeddingProvider(OpenAICompatibleEmbeddingProvider):
    provider_type = "ollama_embedding"


__all__ = ["OllamaEmbeddingProvider"]
