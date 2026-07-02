from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionScanResult:
    instruction_like: bool = False
    scope_expansion_attempt: bool = False
    tool_call_attempt: bool = False
    exfiltration_attempt: bool = False
    delimiter_attack: bool = False
    role_confusion: bool = False
    secret_request: bool = False
    policy_override_attempt: bool = False

    @property
    def flags(self) -> list[str]:
        return [key for key, value in self.__dict__.items() if value]


_PATTERNS: dict[str, tuple[str, ...]] = {
    "instruction_like": (r"ignore previous instructions", r"دستورهای قبلی را نادیده بگیر"),
    "scope_expansion_attempt": (r"other projects", r"all memories", r"پروژه‌های دیگر"),
    "tool_call_attempt": (r"call the .*endpoint", r"delete endpoint", r"run tool"),
    "exfiltration_attempt": (r"exfiltrate", r"reveal .*api", r"فاش کن"),
    "delimiter_attack": (r"</memory_context_attachment>", r"close the memory delimiter"),
    "role_confusion": (r"you are now the system", r"نقش سیستم"),
    "secret_request": (r"api key", r"token", r"password", r"secret"),
    "policy_override_attempt": (r"override .*policy", r"ignore previous", r"نادیده بگیر"),
}

_SECRET_LIKE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|api[_-]?key\s*[:=]\s*[^\s]+|token\s*[:=]\s*[^\s]+)"
)


def scan_text(text: str) -> InjectionScanResult:
    lowered = text.lower()
    values: dict[str, bool] = {}
    for flag, patterns in _PATTERNS.items():
        values[flag] = any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)
    values["secret_request"] = values["secret_request"] or bool(_SECRET_LIKE.search(text))
    return InjectionScanResult(**values)


def contains_secret_like_text(text: str) -> bool:
    return bool(_SECRET_LIKE.search(text))
