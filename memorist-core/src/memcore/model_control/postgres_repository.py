from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from memcore.model_control.providers.base import ProviderHealth
from memcore.model_control.repository import (
    ModelControlRepository,
    PrivacyAcknowledgementRequired,
    _default_cost_profile,
    _default_latency_profile,
    _default_privacy_profile,
    _default_quality_profile,
    _effective_endpoint_is_local,
    _model_role,
    _optional_ijson,
    _privacy_profile_requires_ack,
    built_in_default,
    public_profile,
    requires_privacy_acknowledgement,
)
from memcore.model_control.roles import MODEL_ROLE_SPECS
from memcore.model_control.schemas import (
    CostEstimateRequest,
    ModelProfileCreate,
    ModelProfilePatch,
    PrivacyAcknowledgementRequest,
    UsageEventCreate,
)
from memcore.model_control.security import endpoint_is_local, sanitize_error_message
from memcore.models import ModelProfile, ModelRole, new_uuid, utc_now
from memcore.repositories.domain import RepositoryError
from memcore.validators.ijson import dump_ijson, load_ijson


class PostgresModelControlRepository(ModelControlRepository):
    """PostgreSQL-backed model-control repository for Full Mode."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_profile(self, request: ModelProfileCreate) -> ModelProfile:
        profile = self._profile_from_create_request(request)
        self.connection.execute(
            """
            INSERT INTO model_profiles (
                model_profile_uuid, profile_name, provider, provider_type, provider_name,
                model_name, role, endpoint_url, is_local, endpoint_is_local,
                context_window, max_input_tokens, max_output_tokens,
                supports_structured_output, supports_json_mode, supports_embeddings,
                embedding_dimension, tokenizer_family, quality_profile, latency_profile,
                pricing_jsonb, metadata_jsonb, quality_profile_jsonb, latency_profile_jsonb,
                cost_profile_jsonb, privacy_profile_jsonb, secret_strategy,
                secret_env_var_name, requires_privacy_acknowledgement, is_enabled,
                privacy_acknowledged_at, created_at, updated_at, schema_version
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            _profile_insert_params(profile),
        )
        created = self.get_profile(profile.model_profile_uuid)
        if created is None:
            raise RepositoryError("model profile not found after create")
        return created

    def get_profile(self, model_profile_uuid: str) -> ModelProfile | None:
        row = self.connection.execute(
            "SELECT * FROM model_profiles WHERE model_profile_uuid = %s",
            (model_profile_uuid,),
        ).fetchone()
        return _profile_from_row(row) if row is not None else None

    def list_profiles(self, role: ModelRole | str | None = None) -> list[ModelProfile]:
        if role is None:
            rows = self.connection.execute(
                "SELECT * FROM model_profiles ORDER BY created_at, model_profile_uuid"
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM model_profiles
                WHERE role = %s
                ORDER BY created_at, model_profile_uuid
                """,
                (_model_role(role).value,),
            ).fetchall()
        return [_profile_from_row(row) for row in rows]

    def patch_profile(self, model_profile_uuid: str, request: ModelProfilePatch) -> ModelProfile:
        current = self.get_profile(model_profile_uuid)
        if current is None:
            raise RepositoryError(f"model profile not found: {model_profile_uuid}")
        values = request.model_dump(exclude_unset=True, mode="json")
        privacy_acknowledged = values.pop("privacy_acknowledged", None)
        updates: dict[str, Any] = {}

        direct = {
            "profile_name",
            "provider_name",
            "model_name",
            "endpoint_url",
            "context_window",
            "max_input_tokens",
            "max_output_tokens",
            "supports_structured_output",
            "supports_json_mode",
            "supports_embeddings",
            "embedding_dimension",
            "tokenizer_family",
            "quality_profile",
            "latency_profile",
            "secret_strategy",
            "secret_env_var_name",
            "is_enabled",
        }
        for key in direct & set(values):
            updates[key] = values[key]
        if "provider_type" in values:
            updates["provider_type"] = values["provider_type"]
            updates["provider"] = values.get("provider_name") or values["provider_type"]
        if "provider_name" in values and values["provider_name"] is not None:
            updates["provider"] = values["provider_name"]
        if "role" in values:
            updates["role"] = _model_role(str(values["role"])).value
        if "endpoint_url" in values and "endpoint_is_local" not in values:
            updates["endpoint_is_local"] = endpoint_is_local(values["endpoint_url"])
            updates["is_local"] = updates["endpoint_is_local"]
        if "endpoint_is_local" in values:
            updates["endpoint_is_local"] = values["endpoint_is_local"]
            updates["is_local"] = values["endpoint_is_local"]
            if values["endpoint_is_local"] is False:
                updates["requires_privacy_acknowledgement"] = True
        if "cost_profile" in values:
            updates["cost_profile_jsonb"] = _jsonb(values["cost_profile"])
            updates["pricing_jsonb"] = updates["cost_profile_jsonb"]
        if "privacy_profile" in values:
            privacy_profile = values["privacy_profile"]
            updates["privacy_profile_jsonb"] = _jsonb(privacy_profile)
            if privacy_profile is not None:
                updates["requires_privacy_acknowledgement"] = _privacy_profile_requires_ack(
                    privacy_profile
                )
        if "quality_profile_data" in values:
            updates["quality_profile_jsonb"] = _jsonb(values["quality_profile_data"])
        if "latency_profile_data" in values:
            updates["latency_profile_jsonb"] = _jsonb(values["latency_profile_data"])
        if "metadata" in values:
            updates["metadata_jsonb"] = _jsonb(values["metadata"])
        if privacy_acknowledged:
            updates["privacy_acknowledged_at"] = utc_now()
        updates["updated_at"] = utc_now()
        if not updates:
            return current
        assignments = ", ".join(
            f"{column} = %s::jsonb" if column.endswith("_jsonb") else f"{column} = %s"
            for column in updates
        )
        self.connection.execute(
            f"UPDATE model_profiles SET {assignments} WHERE model_profile_uuid = %s",
            (*updates.values(), model_profile_uuid),
        )
        updated = self.get_profile(model_profile_uuid)
        if updated is None:
            raise RepositoryError(f"model profile not found after update: {model_profile_uuid}")
        return updated

    def set_default(
        self,
        role: ModelRole | str,
        model_profile_uuid: str,
        workspace_uuid: str | None = None,
        project_uuid: str | None = None,
    ) -> dict[str, Any]:
        model_role = _model_role(role)
        profile = self.get_profile(model_profile_uuid)
        if profile is None:
            raise RepositoryError(f"model profile not found: {model_profile_uuid}")
        if not profile.is_enabled:
            raise RepositoryError("disabled model profile cannot be assigned as default")
        if requires_privacy_acknowledgement(profile) and profile.privacy_acknowledged_at is None:
            raise PrivacyAcknowledgementRequired(
                "external or non-local profiles require explicit privacy acknowledgement before use"
            )
        previous = self.resolve_default(model_role, workspace_uuid, project_uuid)
        self.connection.execute(
            """
            DELETE FROM model_role_defaults
            WHERE role = %s
              AND COALESCE(workspace_uuid, '') = COALESCE(%s, '')
              AND COALESCE(project_uuid, '') = COALESCE(%s, '')
            """,
            (model_role.value, workspace_uuid, project_uuid),
        )
        self.connection.execute(
            """
            INSERT INTO model_role_defaults (
                role_default_uuid, role, model_profile_uuid, workspace_uuid,
                project_uuid, created_at, updated_at, schema_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
            """,
            (
                new_uuid(),
                model_role.value,
                model_profile_uuid,
                workspace_uuid,
                project_uuid,
                (created_at := utc_now()),
                created_at,
            ),
        )
        previous_profile_uuid = (
            str(previous["model_profile_uuid"])
            if previous is not None and previous.get("model_profile_uuid") is not None
            else None
        )
        reindex_required = False
        if (
            model_role is ModelRole.EMBEDDING
            and previous_profile_uuid is not None
            and previous_profile_uuid != model_profile_uuid
        ):
            reindex_required = True
            self.mark_embedding_records_stale(previous_profile_uuid)
        return {
            "role": model_role.value,
            "model_profile_uuid": model_profile_uuid,
            "workspace_uuid": workspace_uuid,
            "project_uuid": project_uuid,
            "reindex_required": reindex_required,
        }

    def resolve_default(
        self,
        role: ModelRole | str,
        workspace_uuid: str | None = None,
        project_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        model_role = _model_role(role)
        row = self.connection.execute(
            """
            SELECT p.*
            FROM model_role_defaults d
            JOIN model_profiles p ON p.model_profile_uuid = d.model_profile_uuid
            WHERE d.role = %s
              AND (
                    (d.project_uuid IS NOT NULL AND d.project_uuid = %s)
                 OR (
                    d.project_uuid IS NULL
                    AND d.workspace_uuid IS NOT NULL
                    AND d.workspace_uuid = %s
                 )
                 OR (d.project_uuid IS NULL AND d.workspace_uuid IS NULL)
              )
            ORDER BY
                CASE
                    WHEN d.project_uuid IS NOT NULL THEN 0
                    WHEN d.workspace_uuid IS NOT NULL THEN 1
                    ELSE 2
                END
            LIMIT 1
            """,
            (model_role.value, project_uuid, workspace_uuid),
        ).fetchone()
        return public_profile(_profile_from_row(row)) if row is not None else None

    def record_usage_event(self, event: UsageEventCreate) -> dict[str, Any]:
        model_profile_uuid = event.model_profile_uuid
        if model_profile_uuid is None:
            resolved = self.resolve_default(event.role)
            if resolved is not None and resolved.get("model_profile_uuid") is not None:
                model_profile_uuid = str(resolved["model_profile_uuid"])
        profile = self.get_profile(model_profile_uuid) if model_profile_uuid else None
        fallback = built_in_default(event.role)
        provider_type = event.provider_type or (
            profile.provider_type if profile is not None else str(fallback["provider_type"])
        )
        model_name = event.model_name or (
            profile.model_name if profile is not None else str(fallback["model_name"])
        )
        values = event.model_dump(mode="json")
        values.update(
            {
                "usage_event_uuid": new_uuid(),
                "model_profile_uuid": model_profile_uuid,
                "role": _model_role(event.role).value,
                "provider_type": provider_type,
                "model_name": model_name,
                "error_message_sanitized": sanitize_error_message(event.error_message_sanitized),
                "created_at": utc_now(),
                "schema_version": 1,
            }
        )
        self.connection.execute(
            """
            INSERT INTO model_usage_events (
                usage_event_uuid, model_profile_uuid, role, event_type, input_tokens,
                output_tokens, created_at, schema_version, stage, provider_type,
                model_name, workspace_uuid, project_uuid, session_uuid, message_uuid,
                unit_uuid, annotation_uuid, import_run_uuid, attachment_uuid, job_uuid,
                embedding_count, estimated_cost, latency_ms, status, error_class,
                error_message_sanitized
            )
            VALUES (
                %s, %s, %s, 'model_control', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                values["usage_event_uuid"],
                values["model_profile_uuid"],
                values["role"],
                values["input_tokens"],
                values["output_tokens"],
                values["created_at"],
                values["schema_version"],
                values["stage"],
                values["provider_type"],
                values["model_name"],
                values.get("workspace_uuid"),
                values.get("project_uuid"),
                values.get("session_uuid"),
                values.get("message_uuid"),
                values.get("unit_uuid"),
                values.get("annotation_uuid"),
                values.get("import_run_uuid"),
                values.get("attachment_uuid"),
                values.get("job_uuid"),
                values["embedding_count"],
                values.get("estimated_cost"),
                values.get("latency_ms"),
                values["status"],
                values.get("error_class"),
                values.get("error_message_sanitized"),
            ),
        )
        return values

    def usage_summary(self) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT role, stage, status,
                   COUNT(*) AS count,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(embedding_count), 0) AS embedding_count,
                   COALESCE(SUM(estimated_cost), 0.0) AS estimated_cost
            FROM model_usage_events
            GROUP BY role, stage, status
            ORDER BY role, stage, status
            """
        ).fetchall()
        role_rows = self.connection.execute(
            """
            SELECT role, provider_type, model_name,
                   COUNT(*) AS count,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(embedding_count), 0) AS embedding_count,
                   COALESCE(SUM(estimated_cost), 0.0) AS estimated_cost,
                   SUM(CASE WHEN status IN ('error', 'failed', 'failed_open') THEN 1 ELSE 0 END)
                       AS error_count,
                   MAX(latency_ms) AS max_latency_ms
            FROM model_usage_events
            GROUP BY role, provider_type, model_name
            ORDER BY role, provider_type, model_name
            """
        ).fetchall()
        day_rows = self.connection.execute(
            """
            SELECT CAST(created_at AS DATE) AS day,
                   role,
                   COUNT(*) AS count,
                   COALESCE(SUM(estimated_cost), 0.0) AS estimated_cost
            FROM model_usage_events
            GROUP BY CAST(created_at AS DATE), role
            ORDER BY day DESC, role
            """
        ).fetchall()
        by_role = [dict(row) for row in role_rows]
        for item in by_role:
            item["p95_latency_ms"] = item.pop("max_latency_ms")
        return {
            "items": [dict(row) for row in rows],
            "by_role": by_role,
            "today_by_role": [dict(row) | {"day": str(row["day"])} for row in day_rows],
        }

    def record_health_event(
        self, model_profile_uuid: str, health: ProviderHealth
    ) -> dict[str, Any]:
        profile = self.get_profile(model_profile_uuid)
        if profile is None:
            raise RepositoryError(f"model profile not found: {model_profile_uuid}")
        values = {
            "health_event_uuid": new_uuid(),
            "model_profile_uuid": model_profile_uuid,
            "role": profile.role.value,
            "provider_type": health.provider_type,
            "model_name": health.model_name,
            "status": health.status,
            "latency_ms": health.latency_ms,
            "local_only_safe": health.local_only_safe,
            "detail_sanitized": sanitize_error_message(health.detail),
            "created_at": utc_now(),
            "schema_version": 1,
        }
        self.connection.execute(
            """
            INSERT INTO model_health_events (
                health_event_uuid, model_profile_uuid, role, provider_type, model_name,
                status, latency_ms, local_only_safe, detail_sanitized, created_at, schema_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            tuple(values.values()),
        )
        return values

    def health(self) -> dict[str, Any]:
        profile_count = int(
            self.connection.execute("SELECT COUNT(*) AS count FROM model_profiles").fetchone()[
                "count"
            ]
        )
        stale_embeddings = int(
            self.connection.execute(
                "SELECT COUNT(*) AS count FROM embedding_records WHERE stale_at IS NOT NULL"
            ).fetchone()["count"]
        )
        latest_events = self.connection.execute(
            """
            SELECT h.*
            FROM model_health_events h
            JOIN (
                SELECT model_profile_uuid, MAX(created_at) AS created_at
                FROM model_health_events
                GROUP BY model_profile_uuid
            ) latest
              ON latest.model_profile_uuid = h.model_profile_uuid
             AND latest.created_at = h.created_at
            ORDER BY h.role, h.model_name
            """
        ).fetchall()
        return {
            "status": "ok",
            "profile_count": profile_count,
            "roles": [role.value for role in MODEL_ROLE_SPECS],
            "stale_embedding_records": stale_embeddings,
            "local_first": True,
            "latest_health_events": [dict(row) for row in latest_events],
        }

    def acknowledge_privacy(self, request: PrivacyAcknowledgementRequest) -> dict[str, Any]:
        profile = self.get_profile(request.model_profile_uuid)
        if profile is None:
            raise RepositoryError(f"model profile not found: {request.model_profile_uuid}")
        acknowledged_at = utc_now()
        data_sent_json = _jsonb(request.acknowledged_data_sent)
        values = {
            "ack_uuid": new_uuid(),
            "model_profile_uuid": request.model_profile_uuid,
            "role": profile.role.value,
            "acknowledged_risk_level": request.acknowledged_risk_level,
            "acknowledged_data_sent_jsonb": data_sent_json,
            "acknowledged_at": acknowledged_at,
            "created_at": acknowledged_at,
            "schema_version": 1,
        }
        self.connection.execute(
            """
            INSERT INTO model_privacy_acknowledgements (
                ack_uuid, model_profile_uuid, role, acknowledged_risk_level,
                acknowledged_data_sent_jsonb, acknowledged_at, created_at, schema_version
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            tuple(values.values()),
        )
        self.connection.execute(
            """
            UPDATE model_profiles
            SET privacy_acknowledged_at = %s,
                requires_privacy_acknowledgement = true,
                updated_at = %s
            WHERE model_profile_uuid = %s
            """,
            (acknowledged_at, acknowledged_at, request.model_profile_uuid),
        )
        values["acknowledged_data_sent"] = cast(Any, request.acknowledged_data_sent)
        values.pop("acknowledged_data_sent_jsonb", None)
        return values

    def mark_embedding_records_stale(self, model_profile_uuid: str) -> int:
        cursor = self.connection.execute(
            """
            UPDATE embedding_records
            SET stale_at = %s
            WHERE model_profile_uuid = %s AND stale_at IS NULL
            """,
            (utc_now(), model_profile_uuid),
        )
        return int(cursor.rowcount)

    def _profile_for_cost_estimate(self, request: CostEstimateRequest) -> ModelProfile | None:
        if request.model_profile_uuid:
            profile = self.get_profile(request.model_profile_uuid)
            if profile is None:
                raise RepositoryError(f"model profile not found: {request.model_profile_uuid}")
            return profile
        if request.role is not None:
            resolved = self.resolve_default(request.role)
            if resolved is not None:
                return self.get_profile(str(resolved["model_profile_uuid"]))
        return None

    def _profile_from_create_request(self, request: ModelProfileCreate) -> ModelProfile:
        endpoint_local = _effective_endpoint_is_local(
            request.provider_type.value,
            request.endpoint_url,
            request.endpoint_is_local,
        )
        privacy_profile = request.privacy_profile or _default_privacy_profile(endpoint_local)
        requires_ack = _privacy_profile_requires_ack(privacy_profile) or not endpoint_local
        return ModelProfile(
            profile_name=request.profile_name or request.model_name,
            provider=request.provider_name or request.provider_type.value,
            provider_type=request.provider_type.value,
            provider_name=request.provider_name or request.provider_type.value,
            model_name=request.model_name,
            role=request.role,
            endpoint_url=request.endpoint_url,
            is_local=endpoint_local,
            endpoint_is_local=endpoint_local,
            context_window=request.context_window,
            max_input_tokens=request.max_input_tokens,
            max_output_tokens=request.max_output_tokens,
            supports_structured_output=request.supports_structured_output,
            supports_json_mode=request.supports_json_mode,
            supports_embeddings=request.supports_embeddings,
            embedding_dimension=request.embedding_dimension,
            tokenizer_family=request.tokenizer_family,
            quality_profile=request.quality_profile,
            latency_profile=request.latency_profile,
            pricing_ijson=_optional_ijson("pricing_ijson", request.cost_profile),
            metadata_ijson=_optional_ijson("metadata_ijson", request.metadata),
            quality_profile_ijson=_optional_ijson(
                "metadata_ijson",
                request.quality_profile_data or _default_quality_profile(request.quality_profile),
            ),
            latency_profile_ijson=_optional_ijson(
                "metadata_ijson",
                request.latency_profile_data or _default_latency_profile(request.role),
            ),
            cost_profile_ijson=_optional_ijson(
                "cost_profile_ijson",
                request.cost_profile or _default_cost_profile(endpoint_local),
            ),
            privacy_profile_ijson=_optional_ijson("privacy_profile_ijson", privacy_profile),
            secret_strategy=request.secret_strategy,
            secret_env_var_name=request.secret_env_var_name,
            requires_privacy_acknowledgement=requires_ack,
            is_enabled=request.is_enabled,
            privacy_acknowledged_at=utc_now() if request.privacy_acknowledged else None,
        )


def _profile_insert_params(profile: ModelProfile) -> tuple[Any, ...]:
    return (
        profile.model_profile_uuid,
        profile.profile_name,
        profile.provider,
        profile.provider_type,
        profile.provider_name,
        profile.model_name,
        profile.role.value,
        profile.endpoint_url,
        profile.is_local,
        profile.endpoint_is_local,
        profile.context_window,
        profile.max_input_tokens,
        profile.max_output_tokens,
        profile.supports_structured_output,
        profile.supports_json_mode,
        profile.supports_embeddings,
        profile.embedding_dimension,
        profile.tokenizer_family,
        profile.quality_profile,
        profile.latency_profile,
        _jsonb(load_ijson(profile.pricing_ijson) if profile.pricing_ijson else None),
        _jsonb(load_ijson(profile.metadata_ijson) if profile.metadata_ijson else None),
        _jsonb(
            load_ijson(profile.quality_profile_ijson) if profile.quality_profile_ijson else None
        ),
        _jsonb(
            load_ijson(profile.latency_profile_ijson) if profile.latency_profile_ijson else None
        ),
        _jsonb(load_ijson(profile.cost_profile_ijson) if profile.cost_profile_ijson else None),
        _jsonb(
            load_ijson(profile.privacy_profile_ijson) if profile.privacy_profile_ijson else None
        ),
        profile.secret_strategy,
        profile.secret_env_var_name,
        profile.requires_privacy_acknowledgement,
        profile.is_enabled,
        profile.privacy_acknowledged_at,
        profile.created_at,
        profile.updated_at or profile.created_at,
        profile.schema_version,
    )


def _profile_from_row(row: Any) -> ModelProfile:
    data = dict(row)
    mapped = {
        "model_profile_uuid": data.get("model_profile_uuid"),
        "profile_name": data.get("profile_name"),
        "provider": data.get("provider") or data.get("provider_type") or "unknown",
        "provider_type": data.get("provider_type") or data.get("provider") or "unknown",
        "provider_name": data.get("provider_name"),
        "model_name": data.get("model_name"),
        "role": data.get("role"),
        "endpoint_url": data.get("endpoint_url"),
        "is_local": bool(data.get("is_local", data.get("endpoint_is_local", False))),
        "endpoint_is_local": bool(data.get("endpoint_is_local", data.get("is_local", False))),
        "context_window": data.get("context_window"),
        "max_input_tokens": data.get("max_input_tokens"),
        "max_output_tokens": data.get("max_output_tokens"),
        "supports_structured_output": bool(data.get("supports_structured_output", False)),
        "supports_json_mode": bool(data.get("supports_json_mode", False)),
        "supports_embeddings": bool(data.get("supports_embeddings", False)),
        "embedding_dimension": data.get("embedding_dimension"),
        "tokenizer_family": data.get("tokenizer_family"),
        "quality_profile": data.get("quality_profile") or "unknown",
        "latency_profile": data.get("latency_profile") or "unknown",
        "pricing_ijson": _ijson(data.get("pricing_jsonb")),
        "metadata_ijson": _ijson(data.get("metadata_jsonb")),
        "quality_profile_ijson": _ijson(data.get("quality_profile_jsonb")),
        "latency_profile_ijson": _ijson(data.get("latency_profile_jsonb")),
        "cost_profile_ijson": _ijson(data.get("cost_profile_jsonb")),
        "privacy_profile_ijson": _ijson(data.get("privacy_profile_jsonb")),
        "secret_strategy": data.get("secret_strategy") or "none",
        "secret_env_var_name": data.get("secret_env_var_name"),
        "requires_privacy_acknowledgement": bool(
            data.get("requires_privacy_acknowledgement", False)
        ),
        "is_enabled": bool(data.get("is_enabled", True)),
        "privacy_acknowledged_at": _timestamp(data.get("privacy_acknowledged_at")),
        "created_at": _timestamp(data.get("created_at")),
        "updated_at": _timestamp(data.get("updated_at")),
        "schema_version": data.get("schema_version") or 1,
    }
    return ModelProfile.model_validate(mapped)


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _ijson(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return dump_ijson(parsed)
    return dump_ijson(value)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return normalized.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = str(value)
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text
