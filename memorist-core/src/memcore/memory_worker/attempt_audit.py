"""Append-only audit for contract-bound remote provider attempts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleProviderError,
    ProviderAttempt,
)
from memcore.models import utc_now
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson

ATTEMPT_AUDIT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FrozenProviderExecution:
    stage_execution_uuid: str
    processing_run_uuid: str
    job_uuid: str | None
    source_type: str
    source_uuid: str
    requested_role: str
    effective_role: str
    model_profile_uuid: str | None
    profile_fingerprint: str
    scope_source: str
    inheritance_source: str | None
    provider_type: str
    model_name: str
    capability_mode: str
    prompt_id: str
    prompt_version: str
    contract_hash: str
    input_hash: str
    idempotency_identity: str
    deterministic_fallback_version: str


def stable_stage_execution_uuid(idempotency_identity: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"memorist:stage:{idempotency_identity}"))


def stable_provider_attempt_uuid(stage_execution_uuid: str, attempt_index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"memorist:attempt:{stage_execution_uuid}:{attempt_index}"))


class ProviderAttemptAuditRepository:
    """Reserve-before-call/finalize-after-call persistence for SQLite and PG.

    Reservation is committed before network I/O. If the worker disappears after
    the remote call, the durable row remains ``unknown_completion`` instead of
    falsely claiming success or losing all evidence of a potentially paid call.
    """

    def __init__(
        self,
        connection: Any,
        frozen: FrozenProviderExecution,
        *,
        postgres: bool,
    ) -> None:
        self.connection = connection
        self.frozen = frozen
        self.postgres = postgres

    def reserve(self, attempt_index: int, attempt_kind: str) -> bool:
        if attempt_kind not in {"initial", "repair"}:
            raise ValueError(f"unsupported provider attempt kind: {attempt_kind}")
        attempt_uuid = stable_provider_attempt_uuid(self.frozen.stage_execution_uuid, attempt_index)
        identity = f"{self.frozen.idempotency_identity}:provider:{attempt_index}"
        row: dict[str, Any] = {
            "provider_attempt_uuid": attempt_uuid,
            "stage_execution_uuid": self.frozen.stage_execution_uuid,
            "processing_run_uuid": self.frozen.processing_run_uuid,
            "job_uuid": self.frozen.job_uuid,
            "source_type": self.frozen.source_type,
            "source_uuid": self.frozen.source_uuid,
            "attempt_index": attempt_index,
            "attempt_kind": attempt_kind,
            "requested_role": self.frozen.requested_role,
            "effective_role": self.frozen.effective_role,
            "model_profile_uuid": self.frozen.model_profile_uuid,
            "profile_fingerprint": self.frozen.profile_fingerprint,
            "scope_source": self.frozen.scope_source,
            "inheritance_source": self.frozen.inheritance_source,
            "provider_type": self.frozen.provider_type,
            "model_name": self.frozen.model_name,
            "capability_mode": self.frozen.capability_mode,
            "prompt_id": self.frozen.prompt_id,
            "prompt_version": self.frozen.prompt_version,
            "contract_hash": self.frozen.contract_hash,
            "input_hash": self.frozen.input_hash,
            "transport_status": "unknown_completion",
            "parse_status": "not_observed",
            "canonicalized": False,
            "validation_errors": [],
            "started_at": utc_now(),
            "idempotency_identity": identity,
            "schema_version": ATTEMPT_AUDIT_SCHEMA_VERSION,
        }
        columns = [key for key in row if key != "validation_errors"]
        values = [row[column] for column in columns]
        validation_column = (
            "validation_errors_jsonb" if self.postgres else "validation_errors_ijson"
        )
        columns.append(validation_column)
        values.append(dump_ijson(row["validation_errors"]))
        placeholders = [
            "%s::jsonb"
            if self.postgres and column == validation_column
            else ("%s" if self.postgres else "?")
            for column in columns
        ]
        conflict = (
            "ON CONFLICT (idempotency_identity) DO NOTHING"
            if self.postgres
            else "ON CONFLICT(idempotency_identity) DO NOTHING"
        )
        self.connection.commit()
        cursor = self.connection.execute(
            f"INSERT INTO processing_provider_attempts ({', '.join(columns)}) "
            f"VALUES ({', '.join(placeholders)}) {conflict}",
            tuple(values),
        )
        inserted = int(cursor.rowcount or 0) == 1
        self.connection.commit()
        return inserted

    def finalize(
        self,
        attempt_index: int,
        attempt: ProviderAttempt,
        *,
        schema_valid: bool,
        canonicalized: bool,
        validation_issues: list[dict[str, str]],
    ) -> None:
        output_hash = canonical_hash_ijson(attempt.parsed) if attempt.parsed is not None else None
        parse_status = "parsed" if attempt.parsed is not None else "parse_failed"
        self._finalize(
            attempt_index,
            response_output_hash=output_hash,
            provider_response_id=attempt.provider_response_id,
            http_status=attempt.http_status,
            transport_status="ok",
            parse_status=parse_status,
            schema_valid=schema_valid,
            canonicalized=canonicalized,
            validation_issues=validation_issues,
            input_tokens=attempt.input_tokens,
            output_tokens=attempt.output_tokens,
            latency_ms=attempt.latency_ms,
        )

    def finalize_error(
        self,
        attempt_index: int,
        error: OpenAICompatibleProviderError,
        *,
        latency_ms: int = 0,
    ) -> None:
        self._finalize(
            attempt_index,
            response_output_hash=None,
            provider_response_id=None,
            http_status=error.http_status,
            transport_status=error.category,
            parse_status="error",
            schema_valid=False,
            canonicalized=False,
            validation_issues=[{"path": "(transport)", "code": error.category}],
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
        )

    def count(self) -> int:
        placeholder = "%s" if self.postgres else "?"
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM processing_provider_attempts "
            f"WHERE stage_execution_uuid = {placeholder}",
            (self.frozen.stage_execution_uuid,),
        ).fetchone()
        if row is None:
            return 0
        if isinstance(row, dict):
            return int(row.get("count") or 0)
        try:
            return int(row["count"])
        except (IndexError, KeyError, TypeError):
            return int(row[0])

    def _finalize(
        self,
        attempt_index: int,
        *,
        response_output_hash: str | None,
        provider_response_id: str | None,
        http_status: int | None,
        transport_status: str,
        parse_status: str,
        schema_valid: bool,
        canonicalized: bool,
        validation_issues: list[dict[str, str]],
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
    ) -> None:
        validation = [
            {
                "path": str(item.get("path") or "(root)")[:160],
                "code": str(item.get("code") or "invalid")[:80],
            }
            for item in validation_issues[:20]
        ]
        placeholder = "%s" if self.postgres else "?"
        validation_column = (
            "validation_errors_jsonb" if self.postgres else "validation_errors_ijson"
        )
        validation_value = dump_ijson(validation)
        validation_assignment = (
            f"{validation_column} = {placeholder}::jsonb"
            if self.postgres
            else f"{validation_column} = {placeholder}"
        )
        self.connection.commit()
        self.connection.execute(
            f"""
            UPDATE processing_provider_attempts
            SET response_output_hash = {placeholder},
                provider_response_id = {placeholder},
                http_status = {placeholder},
                transport_status = {placeholder},
                parse_status = {placeholder},
                schema_valid = {placeholder},
                canonicalized = {placeholder},
                {validation_assignment},
                input_tokens = {placeholder},
                output_tokens = {placeholder},
                latency_ms = {placeholder},
                completed_at = {placeholder}
            WHERE stage_execution_uuid = {placeholder}
              AND attempt_index = {placeholder}
              AND completed_at IS NULL
            """,
            (
                response_output_hash,
                provider_response_id,
                http_status,
                transport_status,
                parse_status,
                schema_valid,
                canonicalized,
                validation_value,
                input_tokens,
                output_tokens,
                latency_ms,
                utc_now(),
                self.frozen.stage_execution_uuid,
                attempt_index,
            ),
        )
        self.connection.commit()
