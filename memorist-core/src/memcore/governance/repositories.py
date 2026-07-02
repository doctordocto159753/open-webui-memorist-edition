from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from memcore.models import new_uuid, utc_now
from memcore.repositories.domain import RepositoryError
from memcore.repositories.sqlite import SQLiteRepository
from memcore.validators.ijson import dump_ijson


class GovernanceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def record_delivery_event(
        self,
        input_message_uuid: str,
        memory_uuid: str,
        memory_version_uuid: str,
        delivery_stage: str,
        response_message_uuid: str | None = None,
        attachment_uuid: str | None = None,
        retrieval_run_uuid: str | None = None,
        rank_at_stage: int | None = None,
        score_at_stage: float | None = None,
    ) -> dict[str, Any]:
        event = {
            "delivery_event_uuid": new_uuid(),
            "response_message_uuid": response_message_uuid,
            "input_message_uuid": input_message_uuid,
            "attachment_uuid": attachment_uuid,
            "retrieval_run_uuid": retrieval_run_uuid,
            "memory_uuid": memory_uuid,
            "memory_version_uuid": memory_version_uuid,
            "delivery_stage": delivery_stage,
            "rank_at_stage": rank_at_stage,
            "score_at_stage": score_at_stage,
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("memory_delivery_events", event)
        return event

    def record_attribution(
        self,
        response_message_uuid: str,
        memory_uuid: str,
        memory_version_uuid: str,
        attribution_type: str,
        attribution_status: str,
        method: str,
        response_span_text: str | None = None,
        response_start_char: int | None = None,
        response_end_char: int | None = None,
        confidence: float | None = None,
        evidence: Any = None,
    ) -> dict[str, Any]:
        if response_span_text is not None:
            if response_start_char is None or response_end_char is None:
                raise RepositoryError("response span offsets are required with response_span_text")
            if response_end_char <= response_start_char:
                raise RepositoryError("response span end must be greater than start")
        attribution = {
            "attribution_uuid": new_uuid(),
            "response_message_uuid": response_message_uuid,
            "memory_uuid": memory_uuid,
            "memory_version_uuid": memory_version_uuid,
            "attribution_type": attribution_type,
            "attribution_status": attribution_status,
            "response_span_text": response_span_text,
            "response_start_char": response_start_char,
            "response_end_char": response_end_char,
            "confidence": confidence,
            "method": method,
            "evidence_ijson": dump_ijson(evidence or {}),
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("response_memory_attributions", attribution)
        return attribution

    def create_feedback(
        self,
        feedback_type: str,
        actor_type: str,
        memory_uuid: str | None = None,
        memory_version_uuid: str | None = None,
        attachment_uuid: str | None = None,
        response_message_uuid: str | None = None,
        rating: int | None = None,
        comment: str | None = None,
        actor_uuid: str | None = None,
    ) -> dict[str, Any]:
        feedback = {
            "feedback_uuid": new_uuid(),
            "memory_uuid": memory_uuid,
            "memory_version_uuid": memory_version_uuid,
            "attachment_uuid": attachment_uuid,
            "response_message_uuid": response_message_uuid,
            "feedback_type": feedback_type,
            "rating": rating,
            "comment": comment,
            "actor_type": actor_type,
            "actor_uuid": actor_uuid,
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("memory_feedback", feedback)
        return feedback

    def create_change_request(
        self,
        requested_operation: str,
        actor_type: str,
        memory_uuid: str | None = None,
        proposed_value: Any = None,
        reason: str | None = None,
        actor_uuid: str | None = None,
        impact_preview: Any = None,
    ) -> dict[str, Any]:
        request = {
            "change_request_uuid": new_uuid(),
            "memory_uuid": memory_uuid,
            "requested_operation": requested_operation,
            "proposed_value_ijson": dump_ijson(proposed_value or {}),
            "reason": reason,
            "actor_type": actor_type,
            "actor_uuid": actor_uuid,
            "status": "pending",
            "impact_preview_ijson": dump_ijson(impact_preview or {}),
            "applied_memory_version_uuid": None,
            "created_at": utc_now(),
            "reviewed_at": None,
            "applied_at": None,
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("memory_change_requests", request)
        return request

    def get_change_request(self, request_uuid: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM memory_change_requests WHERE change_request_uuid = ?",
            (request_uuid,),
        ).fetchone()
        if row is None:
            raise RepositoryError(f"Change request not found: {request_uuid}")
        return dict(row)

    def update_change_request(self, request_uuid: str, values: Mapping[str, Any]) -> dict[str, Any]:
        with self.connection:
            self.sqlite.update(
                "memory_change_requests",
                values,
                "change_request_uuid = ?",
                (request_uuid,),
            )
        return self.get_change_request(request_uuid)

    def create_privacy_request(
        self,
        request_uuid: str,
        request_type: str,
        target_type: str,
        requested_scope: Any,
        actor_type: str,
        target_uuid: str | None = None,
        actor_uuid: str | None = None,
        status: str = "preview",
        policy_decision: Any = None,
        confirmation_token_hash: str | None = None,
        confirmation_expires_at: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "privacy_request_uuid": request_uuid,
            "request_type": request_type,
            "target_type": target_type,
            "target_uuid": target_uuid,
            "requested_scope_ijson": dump_ijson(requested_scope),
            "actor_type": actor_type,
            "actor_uuid": actor_uuid,
            "status": status,
            "confirmation_token_hash": confirmation_token_hash,
            "confirmation_expires_at": confirmation_expires_at,
            "policy_decision_ijson": dump_ijson(policy_decision or {}),
            "created_at": utc_now(),
            "confirmed_at": None,
            "completed_at": None,
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("privacy_requests", request)
        return request

    def get_privacy_request(self, request_uuid: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM privacy_requests WHERE privacy_request_uuid = ?",
            (request_uuid,),
        ).fetchone()
        if row is None:
            raise RepositoryError(f"Privacy request not found: {request_uuid}")
        return dict(row)

    def update_privacy_request(
        self, request_uuid: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self.connection:
            self.sqlite.update(
                "privacy_requests",
                values,
                "privacy_request_uuid = ?",
                (request_uuid,),
            )
        return self.get_privacy_request(request_uuid)

    def add_privacy_item(
        self,
        privacy_request_uuid: str,
        storage_type: str,
        record_type: str,
        action: str,
        record_uuid: str | None = None,
        status: str = "pending",
    ) -> dict[str, Any]:
        item = {
            "privacy_request_item_uuid": new_uuid(),
            "privacy_request_uuid": privacy_request_uuid,
            "storage_type": storage_type,
            "record_type": record_type,
            "record_uuid": record_uuid,
            "action": action,
            "status": status,
            "last_error": None,
            "processed_at": None,
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("privacy_request_items", item)
        return item

    def list_privacy_items(self, request_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM privacy_request_items WHERE privacy_request_uuid = ?",
                (request_uuid,),
            )
        ]

    def update_privacy_item(self, item_uuid: str, values: Mapping[str, Any]) -> None:
        with self.connection:
            self.sqlite.update(
                "privacy_request_items",
                values,
                "privacy_request_item_uuid = ?",
                (item_uuid,),
            )

    def create_receipt(
        self,
        privacy_request_uuid: str,
        request_fingerprint: str,
        erased_counts: dict[str, int],
        retained_counts: dict[str, int],
        exceptions: list[dict[str, object]],
        verification: dict[str, object],
    ) -> dict[str, Any]:
        receipt = {
            "erasure_receipt_uuid": new_uuid(),
            "privacy_request_uuid": privacy_request_uuid,
            "request_fingerprint": request_fingerprint,
            "erased_record_counts_ijson": dump_ijson(erased_counts),
            "retained_record_counts_ijson": dump_ijson(retained_counts),
            "exceptions_ijson": dump_ijson(exceptions),
            "verification_ijson": dump_ijson(verification),
            "completed_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("erasure_receipts", receipt)
        return receipt

    def get_receipt(self, request_uuid: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM erasure_receipts WHERE privacy_request_uuid = ?",
            (request_uuid,),
        ).fetchone()
        return dict(row) if row is not None else None


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()
