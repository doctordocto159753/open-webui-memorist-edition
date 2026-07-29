"""Runtime-neutral bounded context resolution for WP02.

The resolver receives canonical storage records through a narrow source
protocol.  It independently re-checks every authority boundary before a record
can enter a model prompt.  Store adapters may narrow their SQL queries for
efficiency, but their query predicates are never the only isolation control.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.semantic_contract import (
    BoundedContextItem,
    SemanticContextBoundary,
)
from memcore.models import SensitivityClass
from memcore.textsemantics import TextEnvelope
from memcore.validators.ijson import canonical_hash_ijson

BOUNDED_CONTEXT_RESOLVER_VERSION = "memorist.semantic_candidate.bounded_context.v1"
_CONTEXT_ITEM_NAMESPACE = uuid.UUID("740a0732-fce8-5f8c-ad65-d5183e30e0bc")
_UNBOUND_USER_PREFIX = "unbound-session:"


@dataclass(frozen=True)
class CurrentContextScope:
    message_uuid: str
    message_version_uuid: str | None
    session_uuid: str
    workspace_uuid: str | None
    project_uuid: str | None
    user_uuid: str | None
    actor_workspace_uuid: str | None
    role: str
    turn_index: int | None
    raw_text: str
    visibility: str = "visible"
    is_deleted: bool = False
    redaction_status: str = "none"


@dataclass(frozen=True)
class PriorContextRecord:
    user_uuid: str | None
    session_uuid: str
    workspace_uuid: str | None
    project_uuid: str | None
    message_uuid: str
    message_version_uuid: str | None
    version_raw_text: str | None
    role: str
    turn_index: int | None
    visibility: str
    is_deleted: bool
    redaction_status: str
    text_unit_uuid: str
    unit_index: int
    raw_start: int
    raw_end: int
    unit_text: str


class BoundedContextSource(Protocol):
    """Read-only store boundary consumed by the shared resolver."""

    def load_current_context_scope(self, message_uuid: str) -> CurrentContextScope: ...

    def list_prior_context_records(
        self,
        scope: CurrentContextScope,
        *,
        scan_limit: int,
    ) -> Sequence[PriorContextRecord]: ...


@dataclass(frozen=True)
class BoundedContextResolution:
    scope: CurrentContextScope
    boundary: SemanticContextBoundary
    items: tuple[BoundedContextItem, ...]
    authority_complete: bool
    exclusion_reason_codes: tuple[str, ...]


class BoundedContextResolver:
    """Resolve the fixed two/six-unit WP02 context window."""

    def resolve(
        self,
        source: BoundedContextSource,
        *,
        message_uuid: str,
        text_envelope: TextEnvelope,
    ) -> BoundedContextResolution:
        scope = source.load_current_context_scope(message_uuid)
        expanded = text_envelope.requires_conversation_context
        effective_limit: Literal[2, 6] = 6 if expanded else 2
        authority_complete = self._scope_authority_complete(scope)
        user_uuid = scope.user_uuid or f"{_UNBOUND_USER_PREFIX}{scope.session_uuid}"
        boundary = SemanticContextBoundary(
            user_uuid=user_uuid,
            session_uuid=scope.session_uuid,
            workspace_uuid=scope.workspace_uuid,
            project_uuid=scope.project_uuid,
            baseline_limit=2,
            effective_limit=effective_limit,
            dependency_expansion=expanded,
        )
        reasons: list[str] = []
        if not authority_complete:
            reasons.append("missing_or_conflicting_session_actor")
            return BoundedContextResolution(
                scope=scope,
                boundary=boundary,
                items=(),
                authority_complete=False,
                exclusion_reason_codes=tuple(reasons),
            )

        records = source.list_prior_context_records(
            scope,
            scan_limit=max(32, effective_limit * 8),
        )
        eligible: list[PriorContextRecord] = []
        for record in records:
            reason = self._ineligible_reason(scope, record)
            if reason is None:
                eligible.append(record)
            else:
                reasons.append(reason)
        eligible.sort(
            key=lambda item: (
                int(item.turn_index or 0),
                item.unit_index,
                item.message_uuid,
                item.text_unit_uuid,
            )
        )
        selected = eligible[-effective_limit:]
        items = tuple(self._to_item(scope, record) for record in selected)
        return BoundedContextResolution(
            scope=scope,
            boundary=boundary,
            items=items,
            authority_complete=True,
            exclusion_reason_codes=tuple(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _scope_authority_complete(scope: CurrentContextScope) -> bool:
        if not scope.user_uuid or scope.turn_index is None:
            return False
        return not (
            scope.workspace_uuid is not None and scope.actor_workspace_uuid != scope.workspace_uuid
        )

    @staticmethod
    def _ineligible_reason(
        scope: CurrentContextScope,
        record: PriorContextRecord,
    ) -> str | None:
        if (
            record.user_uuid != scope.user_uuid
            or record.session_uuid != scope.session_uuid
            or record.workspace_uuid != scope.workspace_uuid
            or record.project_uuid != scope.project_uuid
        ):
            return "cross_authority_boundary"
        if record.message_uuid == scope.message_uuid:
            return "current_message_excluded"
        if (
            record.turn_index is None
            or scope.turn_index is None
            or record.turn_index >= scope.turn_index
        ):
            return "non_prior_turn"
        if record.role not in {"user", "assistant"}:
            # System prompts and tool output are not semantic context.  A future
            # tool-specific allow-list may admit a canonical observation, but
            # unrelated tool output is fail-closed today.
            return "role_excluded"
        if record.visibility != "visible" or record.is_deleted or record.redaction_status != "none":
            return "hidden_deleted_or_redacted"
        if record.message_version_uuid is None or record.version_raw_text is None:
            return "immutable_message_version_missing"
        if (
            record.raw_start < 0
            or record.raw_end <= record.raw_start
            or record.raw_end > len(record.version_raw_text)
            or record.version_raw_text[record.raw_start : record.raw_end] != record.unit_text
        ):
            return "stale_or_invalid_text_unit_span"
        if classify_sensitivity(record.unit_text) is not SensitivityClass.NORMAL:
            return "sensitive_context_excluded"
        return None

    @staticmethod
    def _to_item(
        scope: CurrentContextScope,
        record: PriorContextRecord,
    ) -> BoundedContextItem:
        text_hash = hashlib.sha256(record.unit_text.encode("utf-8")).hexdigest()
        identity_hash = canonical_hash_ijson(
            {
                "resolver_version": BOUNDED_CONTEXT_RESOLVER_VERSION,
                "user_uuid": scope.user_uuid,
                "session_uuid": record.session_uuid,
                "workspace_uuid": record.workspace_uuid,
                "project_uuid": record.project_uuid,
                "message_uuid": record.message_uuid,
                "message_version_uuid": record.message_version_uuid,
                "text_unit_uuid": record.text_unit_uuid,
                "role": record.role,
                "turn_index": record.turn_index,
                "unit_index": record.unit_index,
                "raw_start": record.raw_start,
                "raw_end": record.raw_end,
                "raw_text_hash": text_hash,
            }
        )
        context_item_id = str(uuid.uuid5(_CONTEXT_ITEM_NAMESPACE, identity_hash))
        role = cast(Literal["user", "assistant"], record.role)
        ceiling: Literal["user_explicit", "assistant_claim"] = (
            "user_explicit" if role == "user" else "assistant_claim"
        )
        assert record.turn_index is not None
        return BoundedContextItem(
            context_item_id=context_item_id,
            user_uuid=str(scope.user_uuid),
            session_uuid=record.session_uuid,
            workspace_uuid=record.workspace_uuid,
            project_uuid=record.project_uuid,
            message_uuid=record.message_uuid,
            message_version_uuid=record.message_version_uuid,
            text_unit_uuid=record.text_unit_uuid,
            role=role,
            turn_index=record.turn_index,
            unit_index=record.unit_index,
            raw_start=record.raw_start,
            raw_end=record.raw_end,
            text=record.unit_text,
            raw_text_hash=text_hash,
            source_authority_ceiling=ceiling,
        )
