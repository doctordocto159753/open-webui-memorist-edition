from __future__ import annotations

import sqlite3
from typing import Any

from memcore.model_control.providers.base import ProviderHealth
from memcore.model_control.roles import MODEL_ROLE_SPECS
from memcore.model_control.schemas import (
    CostEstimateRequest,
    ModelProfileCreate,
    ModelProfilePatch,
    PrivacyAcknowledgementRequest,
    ProviderType,
    UsageEventCreate,
)
from memcore.model_control.security import (
    endpoint_is_local,
    redact_endpoint,
    sanitize_error_message,
)
from memcore.models import ModelProfile, ModelRole, new_uuid, utc_now
from memcore.repositories.domain import RepositoryError
from memcore.repositories.sqlite import SQLiteRepository
from memcore.validators.ijson import load_ijson
from memcore.validators.payload_policy import prepare_ijson_field


class PrivacyAcknowledgementRequired(RepositoryError):
    """Raised when a non-local profile is used before explicit acknowledgement."""


class ModelControlRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_profile(self, request: ModelProfileCreate) -> ModelProfile:
        if request.setup_idempotency_key:
            existing = self.connection.execute(
                "SELECT model_profile_uuid FROM model_profiles WHERE setup_idempotency_key = ?",
                (request.setup_idempotency_key,),
            ).fetchone()
            if existing is not None:
                patch_values = request.model_dump(
                    exclude={"setup_idempotency_key"},
                    exclude_unset=True,
                )
                return self.patch_profile(
                    str(existing["model_profile_uuid"]),
                    ModelProfilePatch.model_validate(patch_values),
                )
        endpoint_local = _effective_endpoint_is_local(
            request.provider_type.value,
            request.endpoint_url,
            request.endpoint_is_local,
        )
        privacy_profile = request.privacy_profile or _default_privacy_profile(endpoint_local)
        requires_ack = _privacy_profile_requires_ack(privacy_profile) or not endpoint_local
        profile = ModelProfile(
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
        with self.connection:
            self.sqlite.insert("model_profiles", profile.model_dump(mode="json"))
            if request.setup_idempotency_key:
                self.connection.execute(
                    "UPDATE model_profiles SET setup_idempotency_key = ? "
                    "WHERE model_profile_uuid = ?",
                    (request.setup_idempotency_key, profile.model_profile_uuid),
                )
        return profile

    def get_profile(self, model_profile_uuid: str) -> ModelProfile | None:
        row = self.connection.execute(
            "SELECT * FROM model_profiles WHERE model_profile_uuid = ?",
            (model_profile_uuid,),
        ).fetchone()
        return _profile_from_sqlite_row(row) if row is not None else None

    def list_profiles(self, role: ModelRole | str | None = None) -> list[ModelProfile]:
        if role is None:
            rows = self.connection.execute(
                "SELECT * FROM model_profiles ORDER BY created_at, model_profile_uuid"
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM model_profiles
                WHERE role = ?
                ORDER BY created_at, model_profile_uuid
                """,
                (_model_role(role).value,),
            ).fetchall()
        return [_profile_from_sqlite_row(row) for row in rows]

    def patch_profile(self, model_profile_uuid: str, request: ModelProfilePatch) -> ModelProfile:
        profile = self.get_profile(model_profile_uuid)
        if profile is None:
            raise RepositoryError(f"model profile not found: {model_profile_uuid}")

        values: dict[str, Any] = request.model_dump(exclude_unset=True, mode="json")
        privacy_acknowledged = values.pop("privacy_acknowledged", None)
        if "provider_type" in values:
            values["provider"] = values.get("provider_name") or values["provider_type"]
        if "provider_name" in values and values["provider_name"] is not None:
            values["provider"] = values["provider_name"]
        if "role" in values:
            values["role"] = _model_role(str(values["role"])).value
        if "cost_profile" in values:
            values["cost_profile_ijson"] = _optional_ijson(
                "cost_profile_ijson",
                values.pop("cost_profile"),
            )
            values["pricing_ijson"] = values["cost_profile_ijson"]
        if "privacy_profile" in values:
            privacy_profile = values.pop("privacy_profile")
            values["privacy_profile_ijson"] = _optional_ijson(
                "privacy_profile_ijson",
                privacy_profile,
            )
            if privacy_profile is not None:
                values["requires_privacy_acknowledgement"] = _privacy_profile_requires_ack(
                    privacy_profile
                )
        if "quality_profile_data" in values:
            values["quality_profile_ijson"] = _optional_ijson(
                "metadata_ijson",
                values.pop("quality_profile_data"),
            )
        if "latency_profile_data" in values:
            values["latency_profile_ijson"] = _optional_ijson(
                "metadata_ijson",
                values.pop("latency_profile_data"),
            )
        if "metadata" in values:
            values["metadata_ijson"] = _optional_ijson("metadata_ijson", values.pop("metadata"))
        if "endpoint_url" in values and "endpoint_is_local" not in values:
            values["endpoint_is_local"] = endpoint_is_local(values["endpoint_url"])
            values["is_local"] = values["endpoint_is_local"]
        if "endpoint_is_local" in values:
            values["is_local"] = values["endpoint_is_local"]
            if values["endpoint_is_local"] is False:
                values["requires_privacy_acknowledgement"] = True
        if privacy_acknowledged:
            values["privacy_acknowledged_at"] = utc_now()
        values["updated_at"] = utc_now()

        if not values:
            return profile
        with self.connection:
            self.sqlite.update(
                "model_profiles",
                values,
                "model_profile_uuid = ?",
                (model_profile_uuid,),
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
        if profile.role is not model_role:
            raise RepositoryError("model profile role does not match the requested default role")
        if requires_privacy_acknowledgement(profile) and profile.privacy_acknowledged_at is None:
            raise PrivacyAcknowledgementRequired(
                "external or non-local profiles require explicit privacy acknowledgement before use"
            )

        previous = self.resolve_default(model_role, workspace_uuid, project_uuid)
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM model_role_defaults
                WHERE role = ?
                  AND COALESCE(workspace_uuid, '') = COALESCE(?, '')
                  AND COALESCE(project_uuid, '') = COALESCE(?, '')
                """,
                (model_role.value, workspace_uuid, project_uuid),
            )
            self.sqlite.insert(
                "model_role_defaults",
                {
                    "model_role_default_uuid": new_uuid(),
                    "role": model_role.value,
                    "model_profile_uuid": model_profile_uuid,
                    "workspace_uuid": workspace_uuid,
                    "project_uuid": project_uuid,
                    "created_at": utc_now(),
                    "updated_at": None,
                    "schema_version": 1,
                },
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
            SELECT p.*, d.workspace_uuid AS resolved_workspace_uuid,
                   d.project_uuid AS resolved_project_uuid
            FROM model_role_defaults d
            JOIN model_profiles p ON p.model_profile_uuid = d.model_profile_uuid
            WHERE d.role = ?
              AND (
                    (d.project_uuid IS NOT NULL AND d.project_uuid = ?)
                 OR (
                    d.project_uuid IS NULL
                    AND d.workspace_uuid IS NOT NULL
                    AND d.workspace_uuid = ?
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
        if row is None:
            return None
        values = dict(row)
        resolved_workspace = values.pop("resolved_workspace_uuid", None)
        resolved_project = values.pop("resolved_project_uuid", None)
        payload = public_profile(_profile_from_sqlite_values(values))
        payload["workspace_uuid"] = resolved_workspace
        payload["project_uuid"] = resolved_project
        return payload

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
        values["model_profile_uuid"] = model_profile_uuid
        values["usage_uuid"] = new_uuid()
        values["role"] = _model_role(event.role).value
        values["provider_type"] = provider_type
        values["model_name"] = model_name
        values["error_message_sanitized"] = sanitize_error_message(event.error_message_sanitized)
        values["created_at"] = utc_now()
        values["schema_version"] = 1
        with self.connection:
            self.sqlite.insert("model_usage_events", values)
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
        items = [dict(row) for row in rows]
        role_rows = self.connection.execute(
            """
            SELECT role,
                   provider_type,
                   model_name,
                   COUNT(*) AS count,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(embedding_count), 0) AS embedding_count,
                   COALESCE(SUM(estimated_cost), 0.0) AS estimated_cost,
                   SUM(CASE WHEN status IN ('error', 'failed', 'failed_open') THEN 1 ELSE 0 END)
                       AS error_count,
                   MAX(latency_ms) AS max_latency_ms,
                   MAX(created_at) AS last_used_at
            FROM model_usage_events
            GROUP BY role, provider_type, model_name
            ORDER BY role, provider_type, model_name
            """
        ).fetchall()
        day_rows = self.connection.execute(
            """
            SELECT substr(created_at, 1, 10) AS day,
                   role,
                   COUNT(*) AS count,
                   COALESCE(SUM(estimated_cost), 0.0) AS estimated_cost
            FROM model_usage_events
            GROUP BY day, role
            ORDER BY day DESC, role
            """
        ).fetchall()
        by_role = [dict(row) for row in role_rows]
        for item in by_role:
            item["p95_latency_ms"] = item.pop("max_latency_ms")
        return {
            "items": items,
            "by_role": by_role,
            "today_by_role": [dict(row) for row in day_rows],
        }

    def record_health_event(
        self,
        model_profile_uuid: str,
        health: ProviderHealth,
        test_idempotency_key: str | None = None,
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
            "result_ijson": prepare_ijson_field(
                "metadata_ijson", health.model_dump(mode="json")
            ),
            "test_idempotency_key": test_idempotency_key,
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            if test_idempotency_key:
                existing = self.connection.execute(
                    "SELECT * FROM model_health_events "
                    "WHERE model_profile_uuid = ? AND test_idempotency_key = ?",
                    (model_profile_uuid, test_idempotency_key),
                ).fetchone()
                if existing is not None:
                    return dict(existing)
            self.sqlite.insert("model_health_events", values)
        return values

    def health(self) -> dict[str, Any]:
        profile_count = int(
            self.connection.execute("SELECT COUNT(*) FROM model_profiles").fetchone()[0]
        )
        stale_embeddings = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM embedding_records WHERE stale_at IS NOT NULL"
            ).fetchone()[0]
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

    def privacy_matrix(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for profile in self.list_profiles():
            spec = MODEL_ROLE_SPECS.get(profile.role)
            items.append(
                {
                    "model_profile_uuid": profile.model_profile_uuid,
                    "role": profile.role.value,
                    "provider_type": profile.provider_type,
                    "model_name": profile.model_name,
                    "profile_name": profile.profile_name,
                    "endpoint_url": redact_endpoint(profile.endpoint_url),
                    "endpoint_is_local": profile.endpoint_is_local,
                    "requires_acknowledgement": requires_privacy_acknowledgement(profile),
                    "privacy_acknowledged": profile.privacy_acknowledged_at is not None,
                    "sends_user_content": spec.sends_user_content if spec else False,
                    "sends_memory_content": spec.sends_memory_content if spec else False,
                    "cost_profile": _profile_ijson(profile, "cost_profile_ijson"),
                    "latency_profile": _profile_ijson(profile, "latency_profile_ijson"),
                    "quality_profile": _profile_ijson(profile, "quality_profile_ijson"),
                    "privacy_profile": _profile_ijson(profile, "privacy_profile_ijson"),
                }
            )
        return {"items": items}

    def acknowledge_privacy(self, request: PrivacyAcknowledgementRequest) -> dict[str, Any]:
        profile = self.get_profile(request.model_profile_uuid)
        if profile is None:
            raise RepositoryError(f"model profile not found: {request.model_profile_uuid}")
        acknowledged_at = utc_now()
        values = {
            "ack_uuid": new_uuid(),
            "model_profile_uuid": request.model_profile_uuid,
            "role": profile.role.value,
            "acknowledged_risk_level": request.acknowledged_risk_level,
            "acknowledged_data_sent_ijson": _optional_ijson(
                "payload_ijson",
                request.acknowledged_data_sent,
            ),
            "acknowledged_at": acknowledged_at,
            "created_at": acknowledged_at,
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("model_privacy_acknowledgements", values)
            self.sqlite.update(
                "model_profiles",
                {
                    "privacy_acknowledged_at": acknowledged_at,
                    "requires_privacy_acknowledgement": True,
                    "updated_at": acknowledged_at,
                },
                "model_profile_uuid = ?",
                (request.model_profile_uuid,),
            )
        values["acknowledged_data_sent"] = load_ijson(
            str(values.pop("acknowledged_data_sent_ijson"))
        )
        return values

    def estimate_cost(self, request: CostEstimateRequest) -> dict[str, Any]:
        profile = self._profile_for_cost_estimate(request)
        input_tokens = request.input_tokens
        if input_tokens is None:
            input_tokens = _token_count(request.input_text)
        output_tokens = (
            request.output_tokens
            if request.output_tokens is not None
            else _token_count(request.output_text)
        )
        cost_profile = _profile_ijson(profile, "cost_profile_ijson") if profile else {}
        currency = str(cost_profile.get("currency", "USD"))
        input_per_1k = float(
            cost_profile.get("input_per_1k", cost_profile.get("input_token_cost", 0.0))
        )
        output_per_1k = float(
            cost_profile.get("output_per_1k", cost_profile.get("output_token_cost", 0.0))
        )
        embedding_per_1k = float(
            cost_profile.get(
                "embedding_per_1k",
                cost_profile.get("embedding_unit_cost", 0.0),
            )
        )
        estimated_cost = (
            input_tokens * input_per_1k
            + output_tokens * output_per_1k
            + request.embedding_count * embedding_per_1k
        ) / 1000.0
        return {
            "model_profile_uuid": profile.model_profile_uuid if profile else None,
            "provider_type": profile.provider_type if profile else "disabled",
            "model_name": profile.model_name if profile else "safe-local-disabled",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "embedding_count": request.embedding_count,
            "estimated_cost": round(estimated_cost, 8),
            "currency": currency,
            "method": "heuristic",
        }

    def record_embedding(
        self,
        model_profile_uuid: str,
        source_type: str,
        source_uuid: str,
        content_hash: str,
        vector_store_ref: str,
        embedding_dimension: int | None = None,
    ) -> dict[str, Any]:
        values = {
            "embedding_record_uuid": new_uuid(),
            "model_profile_uuid": model_profile_uuid,
            "source_type": source_type,
            "source_uuid": source_uuid,
            "content_hash": content_hash,
            "embedding_dimension": embedding_dimension,
            "vector_store_ref": vector_store_ref,
            "created_at": utc_now(),
            "stale_at": None,
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("embedding_records", values)
        self.record_usage_event(
            UsageEventCreate(
                role=ModelRole.EMBEDDING,
                stage="embedding_recorded",
                model_profile_uuid=model_profile_uuid,
                embedding_count=1,
                status="ok",
            )
        )
        return values

    def mark_embedding_records_stale(self, model_profile_uuid: str) -> int:
        now = utc_now()
        cursor = self.connection.execute(
            """
            UPDATE embedding_records
            SET stale_at = ?
            WHERE model_profile_uuid = ? AND stale_at IS NULL
            """,
            (now, model_profile_uuid),
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


def requires_privacy_acknowledgement(profile: ModelProfile) -> bool:
    if profile.requires_privacy_acknowledgement:
        return True
    privacy_profile = _profile_ijson(profile, "privacy_profile_ijson")
    if not profile.endpoint_is_local:
        return True
    if bool(privacy_profile.get("requires_user_acknowledgement", False)):
        return True
    return str(privacy_profile.get("risk_level", "low")).lower() in {"medium", "high", "external"}


def public_profile(profile: ModelProfile) -> dict[str, Any]:
    payload = profile.model_dump(mode="json")
    payload["endpoint_url"] = redact_endpoint(profile.endpoint_url)
    payload["cost_profile"] = _profile_ijson(profile, "cost_profile_ijson")
    payload["privacy_profile"] = _profile_ijson(profile, "privacy_profile_ijson")
    payload["quality_profile_data"] = _profile_ijson(profile, "quality_profile_ijson")
    payload["latency_profile_data"] = _profile_ijson(profile, "latency_profile_ijson")
    payload.pop("cost_profile_ijson", None)
    payload.pop("privacy_profile_ijson", None)
    payload.pop("quality_profile_ijson", None)
    payload.pop("latency_profile_ijson", None)
    payload.pop("pricing_ijson", None)
    payload.pop("metadata_ijson", None)
    payload["secret_configured"] = profile.secret_strategy != "none"
    payload.pop("secret_env_var_name", None)
    return payload


def built_in_default(role: ModelRole | str) -> dict[str, Any]:
    model_role = _model_role(role)
    defaults: dict[ModelRole, dict[str, Any]] = {
        ModelRole.MAIN_CHAT_OBSERVED: {
            "provider_type": "unknown",
            "model_name": "selected-in-openwebui",
            "controlled_by_memorist": False,
        },
        ModelRole.PREFLIGHT: {
            "provider_type": "deterministic",
            "model_name": "deterministic_preflight",
            "controlled_by_memorist": True,
        },
        ModelRole.MEMORY_EXTRACTION: {
            "provider_type": "deterministic",
            "model_name": "deterministic_extraction",
            "controlled_by_memorist": True,
        },
        ModelRole.EMBEDDING: {
            "provider_type": "disabled",
            "model_name": "embedding-disabled-lite",
            "controlled_by_memorist": True,
            "reindex_required": False,
        },
    }
    optional_default = {
        "provider_type": "deterministic",
        "model_name": "inherits-memory-extraction",
        "controlled_by_memorist": True,
    }
    selected = defaults.get(model_role, optional_default)
    return {
        "model_profile_uuid": None,
        "role": model_role.value,
        **selected,
        "endpoint_is_local": True,
        "is_enabled": True,
        "built_in": True,
        "cost_profile": _default_cost_profile(True),
        "latency_profile": _default_latency_profile(model_role),
        "quality_profile": _default_quality_profile("unknown"),
        "privacy_profile": _default_privacy_profile(True),
    }


def _effective_endpoint_is_local(
    provider_type: str,
    endpoint_url: str | None,
    explicit_endpoint_is_local: bool | None,
) -> bool:
    if provider_type in {
        ProviderType.DISABLED.value,
        ProviderType.DETERMINISTIC.value,
        ProviderType.LOCAL_EMBEDDING.value,
        ProviderType.UNKNOWN.value,
    }:
        return True
    if explicit_endpoint_is_local is not None:
        return explicit_endpoint_is_local
    return endpoint_is_local(endpoint_url)


def _default_cost_profile(endpoint_local: bool) -> dict[str, object]:
    return {
        "currency": "USD",
        "free_local": endpoint_local,
        "input_token_cost": 0.0,
        "output_token_cost": 0.0,
        "embedding_unit_cost": 0.0,
        "input_per_1k": 0.0,
        "output_per_1k": 0.0,
        "embedding_per_1k": 0.0,
        "estimated_daily_cost": 0.0,
        "soft_warning_limit": None,
        "hard_daily_limit": None,
        "local_compute_note": (
            "No provider billing; local compute cost only." if endpoint_local else None
        ),
    }


def _default_privacy_profile(endpoint_local: bool) -> dict[str, object]:
    return {
        "local_only": endpoint_local,
        "remote_endpoint": not endpoint_local,
        "sends_raw_user_text": not endpoint_local,
        "sends_assistant_text": not endpoint_local,
        "sends_memory_summaries": not endpoint_local,
        "sends_embeddings": False,
        "stores_secrets": False,
        "requires_user_acknowledgement": not endpoint_local,
        "risk_level": "low" if endpoint_local else "external",
    }


def _default_latency_profile(role: ModelRole | str) -> dict[str, object]:
    model_role = _model_role(role)
    return {
        "last_latency_ms": None,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "timeout_ms": 800 if model_role is ModelRole.PREFLIGHT else None,
        "target_latency_ms": 500 if model_role is ModelRole.PREFLIGHT else None,
        "blocking_path": model_role is ModelRole.PREFLIGHT,
        "async_path": model_role is not ModelRole.PREFLIGHT,
    }


def _default_quality_profile(quality_profile: str) -> dict[str, object]:
    return {
        "quality_tier": quality_profile or "unknown",
        "structured_output_reliability": "unknown",
        "recommended_for": [],
        "not_recommended_for": [],
        "last_eval_score": None,
    }


def _privacy_profile_requires_ack(value: dict[str, Any]) -> bool:
    if bool(value.get("requires_user_acknowledgement", False)):
        return True
    if bool(value.get("remote_endpoint", False)):
        return True
    return str(value.get("risk_level", "low")).lower() in {"medium", "high", "external"}


def _optional_ijson(field_name: str, value: Any) -> str | None:
    if value is None:
        return None
    return prepare_ijson_field(field_name, value)


def _profile_ijson(profile: ModelProfile, field_name: str) -> dict[str, Any]:
    text = getattr(profile, field_name)
    if text is None:
        return {}
    value = load_ijson(str(text))
    return value if isinstance(value, dict) else {}


def _model_role(value: ModelRole | str) -> ModelRole:
    return value if isinstance(value, ModelRole) else ModelRole(value)


def _profile_from_sqlite_row(row: sqlite3.Row) -> ModelProfile:
    return _profile_from_sqlite_values(dict(row))


def _profile_from_sqlite_values(values: dict[str, Any]) -> ModelProfile:
    values.pop("setup_idempotency_key", None)
    return ModelProfile.model_validate(values)


def _token_count(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0
