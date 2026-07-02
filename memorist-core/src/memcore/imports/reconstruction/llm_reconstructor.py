class LLMReconstructorUnavailable(RuntimeError):
    """No import reconstruction model is configured in Phase 5 baseline."""


def reconstruct_unstructured_with_model() -> None:
    raise LLMReconstructorUnavailable("LLM transcript reconstruction is intentionally disabled")
