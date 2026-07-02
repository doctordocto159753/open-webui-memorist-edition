from __future__ import annotations

from typing import Any

from memcore.security.injection_scanner import contains_secret_like_text, scan_text

CLOSING_DELIMITER = "</memory_context_attachment>"
ESCAPED_CLOSING_DELIMITER = "&lt;/memory_context_attachment&gt;"


def escape_memory_content(text: str) -> tuple[str, int]:
    count = text.count(CLOSING_DELIMITER)
    return text.replace(CLOSING_DELIMITER, ESCAPED_CLOSING_DELIMITER), count


def attachment_safety_report(
    memories: list[dict[str, Any]],
    allowed_scope: str | None = None,
    allow_sensitive: bool = False,
) -> dict[str, Any]:
    warnings: list[str] = []
    trusted_directives = [
        item for item in memories if item.get("trust_level") == "trusted_directive"
    ]
    untrusted_count = 0
    escaped_count = 0
    sensitive_excluded = 0
    rendered: list[str] = []
    for item in memories:
        text = str(item.get("text", ""))
        if allowed_scope is not None and str(item.get("scope", allowed_scope)) != allowed_scope:
            warnings.append(f"scope_excluded:{item.get('memory_key', '')}")
            continue
        if bool(item.get("sensitive", False)) and not allow_sensitive:
            sensitive_excluded += 1
            continue
        scan = scan_text(text)
        if scan.flags:
            warnings.extend(f"{item.get('memory_key', '')}:{flag}" for flag in scan.flags)
        if item.get("source") == "import" or scan.flags:
            item["trust_level"] = "untrusted_data"
        if item.get("trust_level") != "trusted_directive":
            untrusted_count += 1
        escaped, count = escape_memory_content(text)
        escaped_count += count
        if contains_secret_like_text(escaped):
            warnings.append(f"secret_like_excluded:{item.get('memory_key', '')}")
            continue
        rendered.append(escaped)
    instruction_detected = any("instruction_like" in warning for warning in warnings)
    return {
        "instruction_like_content_detected": instruction_detected,
        "untrusted_memory_count": untrusted_count,
        "trusted_directive_count": len(trusted_directives),
        "escaped_delimiter_count": escaped_count,
        "sensitive_item_excluded_count": sensitive_excluded,
        "security_warnings": warnings,
        "rendered_attachment": "\n".join(rendered),
    }
