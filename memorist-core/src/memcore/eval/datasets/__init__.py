from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memcore.validators.ijson import load_ijson


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    conversation_events: list[dict[str, Any]]
    memory_state: list[dict[str, Any]]
    query: dict[str, Any]
    expected: dict[str, Any]
    threats: dict[str, bool]


def load_dataset(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        payload = load_ijson(line)
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number}: eval case must be an object")
        cases.append(
            EvalCase(
                case_id=str(payload["case_id"]),
                category=str(payload["category"]),
                conversation_events=list(payload.get("conversation_events", [])),
                memory_state=list(payload.get("memory_state", [])),
                query=dict(payload["query"]),
                expected=dict(payload["expected"]),
                threats=dict(payload["threats"]),
            )
        )
    return cases
