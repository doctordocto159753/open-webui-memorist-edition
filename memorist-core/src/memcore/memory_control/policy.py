from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class TurnPolicyMode(StrEnum):
    FULL = "full"
    NO_RECALL = "no_recall"
    PRIVATE = "private"


class MemoristTurnPolicy(BaseModel):
    """Immutable normalized behavior for one turn.

    Runtime profile is deliberately absent. It is server configuration and cannot
    be selected by request metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: TurnPolicyMode
    capture_enabled: bool
    recall_enabled: bool
    attachment_enabled: bool
    private: bool

    @property
    def extraction_enabled(self) -> bool:
        return self.capture_enabled and not self.private

    @property
    def graph_retrieval_enabled(self) -> bool:
        return self.recall_enabled and not self.private


_POLICIES = {
    TurnPolicyMode.FULL: MemoristTurnPolicy(
        mode=TurnPolicyMode.FULL,
        capture_enabled=True,
        recall_enabled=True,
        attachment_enabled=True,
        private=False,
    ),
    TurnPolicyMode.NO_RECALL: MemoristTurnPolicy(
        mode=TurnPolicyMode.NO_RECALL,
        capture_enabled=True,
        recall_enabled=False,
        attachment_enabled=False,
        private=False,
    ),
    TurnPolicyMode.PRIVATE: MemoristTurnPolicy(
        mode=TurnPolicyMode.PRIVATE,
        capture_enabled=False,
        recall_enabled=False,
        attachment_enabled=False,
        private=True,
    ),
}


def normalize_turn_policy(value: str | TurnPolicyMode | None) -> MemoristTurnPolicy:
    if value is None:
        return _POLICIES[TurnPolicyMode.FULL]
    try:
        mode = TurnPolicyMode(str(value).strip().lower())
    except ValueError as error:
        raise ValueError("turn_policy must be full, no_recall, or private") from error
    return _POLICIES[mode]


def reject_runtime_override(payload: dict[str, Any] | None) -> None:
    if not payload:
        return
    forbidden = {"runtime_profile", "canonical_store", "db_path", "postgres_dsn"}
    supplied = sorted(forbidden.intersection(payload))
    if supplied:
        raise ValueError(
            f"server-controlled fields are not request-selectable: {', '.join(supplied)}"
        )
