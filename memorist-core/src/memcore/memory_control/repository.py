from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from memcore.memory_control.policy import MemoristTurnPolicy, normalize_turn_policy
from memcore.models import new_uuid, utc_now
from memcore.validators.ijson import dump_ijson


@dataclass(frozen=True)
class ResolvedTurnPolicy:
    policy: MemoristTurnPolicy
    source: str
    attachment_review: bool


class MemoryControlRepository:
    def __init__(self, connection: Any, runtime_profile: str, *, autocommit: bool = True) -> None:
        self.connection = connection
        self.runtime_profile = runtime_profile
        self.autocommit = autocommit

    def _commit(self) -> None:
        if self.autocommit:
            self.connection.commit()

    def resolve_policy(
        self,
        *,
        system_default: str,
        workspace_uuid: str | None,
        user_uuid: str | None,
        chat_uuid: str | None,
        turn_override: str | None,
        attachment_review_override: bool | None = None,
    ) -> ResolvedTurnPolicy:
        selected = system_default
        source = "system"
        attachment_review = False
        system_row = self._policy_row("system", "system", workspace_uuid)
        if system_row is not None:
            selected = str(system_row["turn_policy"])
            attachment_review = bool(system_row["attachment_review"])
        if user_uuid:
            user_row = self._policy_row("user", user_uuid, workspace_uuid)
            if user_row is not None:
                selected = str(user_row["turn_policy"])
                attachment_review = bool(user_row["attachment_review"])
                source = "user"
        if chat_uuid:
            chat_row = self._policy_row("chat", chat_uuid, workspace_uuid)
            if chat_row is not None:
                selected = str(chat_row["turn_policy"])
                attachment_review = bool(chat_row["attachment_review"])
                source = "chat"
        if turn_override is not None:
            selected = turn_override
            source = "turn"
        if attachment_review_override is not None:
            attachment_review = attachment_review_override
        return ResolvedTurnPolicy(
            policy=normalize_turn_policy(selected),
            source=source,
            attachment_review=attachment_review,
        )

    def set_default(
        self,
        *,
        scope_type: str,
        scope_uuid: str,
        workspace_uuid: str | None,
        turn_policy: str,
        attachment_review: bool,
    ) -> dict[str, Any]:
        if scope_type not in {"system", "user", "chat"}:
            raise ValueError("scope_type must be system, user, or chat")
        normalized = normalize_turn_policy(turn_policy)
        existing = self._policy_row(scope_type, scope_uuid, workspace_uuid)
        now = utc_now()
        if existing is None:
            row = {
                "policy_default_uuid": new_uuid(),
                "scope_type": scope_type,
                "scope_uuid": scope_uuid,
                "workspace_uuid": workspace_uuid,
                "turn_policy": normalized.mode.value,
                "attachment_review": attachment_review,
                "created_at": now,
                "updated_at": now,
                "schema_version": 1,
            }
            self._insert("memorist_policy_defaults", row)
        else:
            self.connection.execute(
                """
                UPDATE memorist_policy_defaults
                SET turn_policy = ?, attachment_review = ?, updated_at = ?
                WHERE policy_default_uuid = ?
                """,
                (normalized.mode.value, attachment_review, now, existing["policy_default_uuid"]),
            )
            row = {
                **dict(existing),
                "turn_policy": normalized.mode.value,
                "attachment_review": attachment_review,
                "updated_at": now,
            }
        self._commit()
        return row

    def record_turn_contract(
        self,
        *,
        input_message_uuid: str,
        session_uuid: str,
        workspace_uuid: str | None,
        user_uuid: str | None,
        chat_uuid: str | None,
        resolved: ResolvedTurnPolicy,
    ) -> None:
        if resolved.policy.private:
            return
        self.connection.execute(
            """
            INSERT INTO memorist_turn_contracts (
                turn_contract_uuid, input_message_uuid, session_uuid, workspace_uuid,
                user_uuid, chat_uuid, turn_policy, capture_enabled, recall_enabled,
                attachment_enabled, attachment_review, policy_source, runtime_profile,
                created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT (input_message_uuid) DO NOTHING
            """,
            (
                new_uuid(),
                input_message_uuid,
                session_uuid,
                workspace_uuid,
                user_uuid,
                chat_uuid,
                resolved.policy.mode.value,
                resolved.policy.capture_enabled,
                resolved.policy.recall_enabled,
                resolved.policy.attachment_enabled,
                resolved.attachment_review,
                resolved.source,
                self.runtime_profile,
                utc_now(),
            ),
        )
        self._commit()

    def get_turn_contract(self, input_message_uuid: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM memorist_turn_contracts WHERE input_message_uuid = ?",
            (input_message_uuid,),
        ).fetchone()
        return dict(row) if row is not None else None

    def audit(
        self,
        event_type: str,
        policy: MemoristTurnPolicy,
        *,
        session_uuid: str | None = None,
        input_message_uuid: str | None = None,
        workspace_uuid: str | None = None,
        user_uuid: str | None = None,
        attachment_uuid: str | None = None,
        degraded_reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if policy.private:
            return
        self._insert(
            "memorist_policy_audit_events",
            {
                "audit_event_uuid": new_uuid(),
                "event_type": event_type,
                "input_message_uuid": input_message_uuid,
                "session_uuid": session_uuid,
                "workspace_uuid": workspace_uuid,
                "user_uuid": user_uuid,
                "attachment_uuid": attachment_uuid,
                "turn_policy": policy.mode.value,
                "runtime_profile": self.runtime_profile,
                "degraded_reason": degraded_reason,
                "detail_ijson": dump_ijson(detail or {}),
                "created_at": utc_now(),
                "schema_version": 1,
            },
        )
        self._commit()

    def attachment_for_actor(
        self,
        attachment_uuid: str,
        *,
        user_uuid: str,
        workspace_uuid: str | None,
    ) -> dict[str, Any] | None:
        if not user_uuid or not workspace_uuid:
            return None
        row = self.connection.execute(
            """
            SELECT * FROM memory_context_attachments
            WHERE attachment_uuid = ?
              AND owner_user_uuid = ?
              AND owner_user_uuid IS NOT NULL
              AND workspace_uuid IS NOT NULL
              AND workspace_uuid = ?
            """,
            (attachment_uuid, user_uuid, workspace_uuid),
        ).fetchone()
        return dict(row) if row is not None else None

    def transition_attachment(
        self,
        attachment_uuid: str,
        lifecycle_status: str,
        *,
        user_uuid: str,
        workspace_uuid: str | None,
        idempotency_key: str,
        response_message_uuid: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "prepared": {
                "approved",
                "delivered",
                "suppressed",
                "cancelled_before_send",
                "user_rejected",
            },
            "approved": {"delivered", "suppressed", "cancelled_before_send", "user_rejected"},
            "delivered": {"used_for_response"},
            "suppressed": set(),
            "cancelled_before_send": set(),
            "user_rejected": set(),
            "used_for_response": set(),
        }
        attachment = self.attachment_for_actor(
            attachment_uuid, user_uuid=user_uuid, workspace_uuid=workspace_uuid
        )
        if attachment is None:
            raise LookupError("attachment not found")
        if lifecycle_status == "delivered":
            if _is_expired(attachment.get("expires_at")):
                raise ValueError("attachment expired before delivery")
            if str(attachment.get("status") or "active") != "active":
                raise ValueError("inactive attachment cannot be delivered")
            if attachment.get("stale_reason"):
                raise ValueError("stale attachment cannot be delivered")
            if (
                bool(attachment.get("attachment_review"))
                and str(attachment.get("lifecycle_status") or "prepared") != "approved"
            ):
                raise ValueError("attachment approval required before delivery")
        current = str(attachment.get("lifecycle_status") or "prepared")
        existing = self.connection.execute(
            """
            SELECT * FROM memorist_attachment_lifecycle_events
            WHERE attachment_uuid = ? AND lifecycle_status = ? AND idempotency_key = ?
            """,
            (attachment_uuid, lifecycle_status, idempotency_key),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        if lifecycle_status == "user_rejected":
            if current not in {"delivered", "used_for_response"}:
                raise ValueError("only a delivered attachment can be rejected")
            now = utc_now()
            self.connection.execute(
                "UPDATE memory_context_attachments SET user_disposition = 'rejected', "
                "user_disposition_at = ? WHERE attachment_uuid = ?",
                (now, attachment_uuid),
            )
            event = {
                "lifecycle_event_uuid": new_uuid(),
                "attachment_uuid": attachment_uuid,
                "lifecycle_status": lifecycle_status,
                "idempotency_key": idempotency_key,
                "user_uuid": user_uuid,
                "workspace_uuid": workspace_uuid,
                "response_message_uuid": response_message_uuid,
                "detail_ijson": dump_ijson(detail or {}),
                "created_at": now,
                "schema_version": 1,
            }
            self._insert("memorist_attachment_lifecycle_events", event)
            input_message_uuid = attachment.get("input_message_uuid")
            contract = (
                self.get_turn_contract(str(input_message_uuid))
                if input_message_uuid is not None
                else None
            )
            if contract is not None:
                self.audit(
                    "attachment_user_rejected",
                    normalize_turn_policy(str(contract["turn_policy"])),
                    session_uuid=str(attachment["session_uuid"]),
                    input_message_uuid=str(input_message_uuid),
                    workspace_uuid=workspace_uuid,
                    user_uuid=user_uuid,
                    attachment_uuid=attachment_uuid,
                    detail={"lifecycle_event_uuid": event["lifecycle_event_uuid"]},
                )
            else:
                self._commit()
            return event
        if lifecycle_status == current:
            canonical = self.connection.execute(
                """
                SELECT * FROM memorist_attachment_lifecycle_events
                WHERE attachment_uuid = ? AND lifecycle_status = ?
                ORDER BY created_at LIMIT 1
                """,
                (attachment_uuid, lifecycle_status),
            ).fetchone()
            if canonical is not None:
                return dict(canonical)
        if lifecycle_status not in allowed.get(current, set()):
            raise ValueError(f"invalid attachment transition: {current} -> {lifecycle_status}")
        now = utc_now()
        timestamp_column = {
            "approved": "approved_at",
            "delivered": "delivered_at",
            "suppressed": "suppressed_at",
            "cancelled_before_send": "cancelled_at",
            "user_rejected": "cancelled_at",
        }.get(lifecycle_status)
        if timestamp_column:
            updated = self.connection.execute(
                "UPDATE memory_context_attachments "
                f"SET lifecycle_status = ?, {timestamp_column} = ? "
                "WHERE attachment_uuid = ? AND lifecycle_status = ?",
                (lifecycle_status, now, attachment_uuid, current),
            )
        else:
            updated = self.connection.execute(
                "UPDATE memory_context_attachments SET lifecycle_status = ? "
                "WHERE attachment_uuid = ? AND lifecycle_status = ?",
                (lifecycle_status, attachment_uuid, current),
            )
        if updated.rowcount != 1:
            concurrent = self.connection.execute(
                """
                SELECT * FROM memorist_attachment_lifecycle_events
                WHERE attachment_uuid = ? AND lifecycle_status = ?
                ORDER BY created_at LIMIT 1
                """,
                (attachment_uuid, lifecycle_status),
            ).fetchone()
            if concurrent is not None:
                return dict(concurrent)
            raise ValueError("attachment state changed concurrently")
        event = {
            "lifecycle_event_uuid": new_uuid(),
            "attachment_uuid": attachment_uuid,
            "lifecycle_status": lifecycle_status,
            "idempotency_key": idempotency_key,
            "user_uuid": user_uuid,
            "workspace_uuid": workspace_uuid,
            "response_message_uuid": response_message_uuid,
            "detail_ijson": dump_ijson(detail or {}),
            "created_at": now,
            "schema_version": 1,
        }
        self._insert("memorist_attachment_lifecycle_events", event)
        self._commit()
        input_message_uuid = attachment.get("input_message_uuid")
        contract = (
            self.get_turn_contract(str(input_message_uuid))
            if input_message_uuid is not None
            else None
        )
        if contract is not None:
            self.audit(
                f"attachment_{lifecycle_status}",
                normalize_turn_policy(str(contract["turn_policy"])),
                session_uuid=str(attachment["session_uuid"]),
                input_message_uuid=str(input_message_uuid),
                workspace_uuid=workspace_uuid,
                user_uuid=user_uuid,
                attachment_uuid=attachment_uuid,
                detail={"lifecycle_event_uuid": event["lifecycle_event_uuid"]},
            )
        return event

    def _policy_row(
        self, scope_type: str, scope_uuid: str, workspace_uuid: str | None
    ) -> Any | None:
        return self.connection.execute(
            """
            SELECT * FROM memorist_policy_defaults
            WHERE scope_type = ? AND scope_uuid = ?
              AND COALESCE(workspace_uuid, '') = COALESCE(?, '')
            """,
            (scope_type, scope_uuid, workspace_uuid),
        ).fetchone()

    def _insert(self, table: str, values: dict[str, Any]) -> None:
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )


def _is_expired(value: Any) -> bool:
    if value is None:
        return False
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)
