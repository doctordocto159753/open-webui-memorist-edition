from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.client import MemoristClient  # noqa: E402
from shared.config import load_config  # noqa: E402
from shared.errors import sanitize_error  # noqa: E402


class Tools:
    def memorist_status(self) -> str:
        config = load_config()
        if not config.enabled:
            return "Memorist: disabled"
        try:
            status = MemoristClient(config).status()
        except Exception as error:
            return f"Memorist Core: disconnected ({sanitize_error(error)})"
        safe = _safe_status(status)
        return json.dumps(safe, ensure_ascii=False, sort_keys=True)


def _safe_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "Memorist Core": status.get("memorist_core", "unknown"),
        "SQLite": status.get("sqlite", "unknown"),
        "Graph backend": status.get("graph_backend", "unknown"),
        "Memory mode": status.get("memory_mode", "unknown"),
        "Preflight": status.get("preflight", "unknown"),
        "Last attachment": status.get("last_attachment"),
        "Last error": status.get("last_error"),
    }
