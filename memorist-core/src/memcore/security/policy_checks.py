from __future__ import annotations

from typing import Any

from memcore.security.attachment_safety import attachment_safety_report


def assert_attachment_policy(memories: list[dict[str, Any]], allowed_scope: str) -> dict[str, Any]:
    report = attachment_safety_report(memories, allowed_scope=allowed_scope)
    rendered = str(report["rendered_attachment"])
    failures: list[str] = []
    if report["trusted_directive_count"]:
        malicious_directives = [
            item
            for item in memories
            if item.get("trust_level") == "trusted_directive" and item.get("source") == "import"
        ]
        if malicious_directives:
            failures.append("imported_content_became_trusted_directive")
    if "</memory_context_attachment>" in rendered:
        failures.append("delimiter_escaping_failed")
    if "sk-" in rendered or "api_key" in rendered.lower():
        failures.append("secret_like_text_rendered")
    if _rendered_cross_scope(memories, allowed_scope, rendered):
        failures.append("scope_expanded")
    report["failures"] = failures
    report["passed"] = not failures
    return report


def _rendered_cross_scope(
    memories: list[dict[str, Any]], allowed_scope: str, rendered: str
) -> bool:
    for item in memories:
        key = str(item.get("memory_key", ""))
        if key and str(item.get("scope")) != allowed_scope and key in rendered:
            return True
    return False
