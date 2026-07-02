from __future__ import annotations

import sqlite3
from typing import Any

from memcore.governance.repositories import GovernanceRepository


def privacy_request_closure(
    connection: sqlite3.Connection,
    request_uuid: str,
) -> dict[str, Any]:
    repository = GovernanceRepository(connection)
    request = repository.get_privacy_request(request_uuid)
    items = repository.list_privacy_items(request_uuid)
    counts: dict[str, int] = {}
    for item in items:
        key = f"{item['storage_type']}:{item['record_type']}:{item['action']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "privacy_request_uuid": request_uuid,
        "request_type": request["request_type"],
        "target_type": request["target_type"],
        "target_uuid": request["target_uuid"],
        "status": request["status"],
        "item_count": len(items),
        "counts": counts,
        "items": [
            {
                "storage_type": item["storage_type"],
                "record_type": item["record_type"],
                "record_uuid": item["record_uuid"],
                "action": item["action"],
                "status": item["status"],
            }
            for item in items
        ],
    }

