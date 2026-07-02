import re
from datetime import datetime, timedelta


def resolve_temporal_expressions(
    text: str, anchor_timestamp: str | None
) -> list[dict[str, object]]:
    lowered = text.lower()
    expressions: list[dict[str, object]] = []
    if "tomorrow" in lowered:
        expressions.append(_resolve_relative("tomorrow", anchor_timestamp, days=1))
    if "yesterday" in lowered:
        expressions.append(_resolve_relative("yesterday", anchor_timestamp, days=-1))
    if "last month" in lowered:
        expressions.append(
            {
                "phrase": "last month",
                "normalized": None,
                "precision": "month",
                "anchor_timestamp": anchor_timestamp,
                "certain": False,
            }
        )
    for match in re.finditer(r"\b20\d{2}-\d{2}-\d{2}\b", text):
        expressions.append(
            {
                "phrase": match.group(0),
                "normalized": f"{match.group(0)}T00:00:00Z",
                "precision": "day",
                "anchor_timestamp": anchor_timestamp,
                "certain": True,
            }
        )
    return expressions


def _resolve_relative(
    phrase: str,
    anchor_timestamp: str | None,
    days: int,
) -> dict[str, object]:
    if anchor_timestamp is None:
        return {
            "phrase": phrase,
            "normalized": None,
            "precision": "day",
            "anchor_timestamp": None,
            "certain": False,
        }
    anchor = datetime.fromisoformat(anchor_timestamp.replace("Z", "+00:00"))
    normalized = (anchor + timedelta(days=days)).date().isoformat()
    return {
        "phrase": phrase,
        "normalized": f"{normalized}T00:00:00Z",
        "precision": "day",
        "anchor_timestamp": anchor_timestamp,
        "certain": True,
    }
