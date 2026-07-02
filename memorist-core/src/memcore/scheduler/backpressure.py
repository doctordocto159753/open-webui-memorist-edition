from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BackpressureState:
    state: str = "idle"
    reason: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"state": self.state, "reason": self.reason}
