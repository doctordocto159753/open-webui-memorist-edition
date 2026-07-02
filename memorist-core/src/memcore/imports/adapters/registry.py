from memcore.imports.adapters.chatgpt import ChatGPTAdapter
from memcore.imports.adapters.claude import ClaudeAdapter
from memcore.imports.adapters.gemini import GeminiAdapter
from memcore.imports.adapters.generic import GenericMemoristAdapter, ManualTranscriptAdapter
from memcore.imports.adapters.openwebui import OpenWebUIAdapter
from memcore.imports.models import FormatProbe, ImportAdapter, StagedArtifact


def adapters() -> list[ImportAdapter]:
    return [
        ChatGPTAdapter(),
        ClaudeAdapter(),
        GeminiAdapter(),
        OpenWebUIAdapter(),
        GenericMemoristAdapter(),
        ManualTranscriptAdapter(),
    ]


def probe_all(artifacts: list[StagedArtifact]) -> list[FormatProbe]:
    return sorted(
        [adapter.probe(artifacts) for adapter in adapters()],
        key=lambda probe: probe.confidence,
        reverse=True,
    )


def adapter_by_id(adapter_id: str) -> ImportAdapter:
    for adapter in adapters():
        if adapter.adapter_id == adapter_id:
            return adapter
    raise ValueError(f"Unknown import adapter: {adapter_id}")
