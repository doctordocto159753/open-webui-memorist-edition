INSTRUCTION_LIKE_PATTERNS = (
    "ignore previous instructions",
    "act as system",
    "reveal hidden context",
    "call a tool",
    "change the system prompt",
    "</memory_context>",
)


def instruction_like_content_detected(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in INSTRUCTION_LIKE_PATTERNS)
