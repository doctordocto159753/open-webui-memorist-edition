from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from memcore.config import Settings, get_settings
from memcore.model_control.postgres_repository import PostgresModelControlRepository
from memcore.model_control.registry import test_profile_health
from memcore.model_control.repository import (
    ModelControlRepository,
    PrivacyAcknowledgementRequired,
    built_in_default,
    public_profile,
)
from memcore.model_control.roles import list_role_specs
from memcore.model_control.schemas import (
    CostEstimateRequest,
    ModelProfileCreate,
    ModelProfilePatch,
    ModelRoleDefaultSet,
    PrivacyAcknowledgementRequest,
    ProfileTestRequest,
)
from memcore.model_control.security import sanitize_error_message
from memcore.model_control.setup import build_setup_status
from memcore.storage.postgres.migrations import apply_postgres_migrations
from memcore.storage.sqlite import connect

router = APIRouter(prefix="/memcore/model-control", tags=["Model Control Plane"])


@router.get("/roles", response_model=None)
def list_roles() -> dict[str, Any]:
    return {"items": list_role_specs()}


@router.get("/setup/status", response_model=None)
def setup_status(workspace_uuid: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    with _connection() as connection:
        return build_setup_status(
            _repository(connection),
            runtime_profile=settings.runtime_profile,
            workspace_uuid=workspace_uuid,
        )


@router.get("/profiles", response_model=None)
def list_profiles(role: str | None = Query(default=None)) -> dict[str, Any]:
    with _connection() as connection:
        try:
            profiles = _repository(connection).list_profiles(role)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"items": [public_profile(profile) for profile in profiles]}


@router.post("/profiles", response_model=None)
def create_profile(request: dict[str, Any]) -> dict[str, Any]:
    try:
        validated = ModelProfileCreate.model_validate(request)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_safe_validation_detail(error)) from error
    with _connection() as connection:
        try:
            profile = _repository(connection).create_profile(validated)
            return public_profile(profile)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=sanitize_error_message(str(error)),
            ) from error


@router.get("/profiles/{model_profile_uuid}", response_model=None)
def get_profile(model_profile_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        profile = _repository(connection).get_profile(model_profile_uuid)
        if profile is None:
            raise HTTPException(status_code=404, detail="model profile not found")
        return public_profile(profile)


@router.patch("/profiles/{model_profile_uuid}", response_model=None)
def patch_profile(model_profile_uuid: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        validated = ModelProfilePatch.model_validate(request)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_safe_validation_detail(error)) from error
    with _connection() as connection:
        try:
            profile = _repository(connection).patch_profile(model_profile_uuid, validated)
            return public_profile(profile)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=sanitize_error_message(str(error)),
            ) from error


@router.post("/profiles/{model_profile_uuid}/test", response_model=None)
def test_profile(model_profile_uuid: str, request: ProfileTestRequest) -> dict[str, Any]:
    with _connection() as connection:
        repository = _repository(connection)
        profile = repository.get_profile(model_profile_uuid)
        if profile is None:
            raise HTTPException(status_code=404, detail="model profile not found")
        health = test_profile_health(profile, timeout_seconds=request.timeout_ms / 1000)
        repository.record_health_event(model_profile_uuid, health)
        return {
            "model_profile_uuid": model_profile_uuid,
            "health": health.model_dump(mode="json"),
        }


@router.get("/defaults", response_model=None)
def get_defaults(
    role: str | None = None,
    workspace_uuid: str | None = None,
    project_uuid: str | None = None,
) -> dict[str, Any]:
    with _connection() as connection:
        repository = _repository(connection)
        if role is not None:
            try:
                resolved = repository.resolve_default(role, workspace_uuid, project_uuid)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            return {"item": resolved or built_in_default(role)}
        return {
            "items": [
                {
                    "role": row["role"],
                    "model_profile_uuid": row["model_profile_uuid"],
                    "workspace_uuid": row["workspace_uuid"],
                    "project_uuid": row["project_uuid"],
                    "created_at": row["created_at"],
                }
                for row in connection.execute(
                    """
                    SELECT *
                    FROM model_role_defaults
                    ORDER BY role, COALESCE(workspace_uuid, ''), COALESCE(project_uuid, '')
                    """
                )
            ]
        }


@router.post("/defaults", response_model=None)
def set_default(request: ModelRoleDefaultSet) -> dict[str, Any]:
    with _connection() as connection:
        try:
            return _repository(connection).set_default(
                request.role,
                request.model_profile_uuid,
                workspace_uuid=request.workspace_uuid,
                project_uuid=request.project_uuid,
            )
        except PrivacyAcknowledgementRequired as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/usage", response_model=None)
def get_usage() -> dict[str, Any]:
    with _connection() as connection:
        return _repository(connection).usage_summary()


@router.get("/health", response_model=None)
def get_model_control_health() -> dict[str, Any]:
    with _connection() as connection:
        return _repository(connection).health()


@router.get("/privacy", response_model=None)
def get_privacy_matrix() -> dict[str, Any]:
    with _connection() as connection:
        return _repository(connection).privacy_matrix()


@router.post("/privacy/acknowledge", response_model=None)
def acknowledge_privacy(request: PrivacyAcknowledgementRequest) -> dict[str, Any]:
    with _connection() as connection:
        try:
            return _repository(connection).acknowledge_privacy(request)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/estimate-cost", response_model=None)
def estimate_cost(request: CostEstimateRequest) -> dict[str, Any]:
    with _connection() as connection:
        try:
            return _repository(connection).estimate_cost(request)
        except ValueError as error:
            detail = sanitize_error_message(str(error))
            raise HTTPException(status_code=400, detail=detail) from error



def _safe_validation_detail(error: ValidationError) -> str:
    details: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "profile"
        message = sanitize_error_message(str(item.get("msg", "invalid value")))
        details.append(f"{location}: {message}")
    return "; ".join(details)[:500] or "invalid model profile configuration"


def _is_full_postgres(settings: Settings) -> bool:
    return settings.runtime_profile == "full" and settings.canonical_store == "postgres"


def _repository(connection: Any) -> ModelControlRepository | PostgresModelControlRepository:
    settings = get_settings()
    if _is_full_postgres(settings):
        return PostgresModelControlRepository(connection)
    return ModelControlRepository(connection)


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    if _is_full_postgres(settings):
        if not settings.postgres_dsn:
            raise RuntimeError("postgres_dsn is required for Full Mode model-control routes")
        psycopg = __import__("psycopg")
        rows = __import__("psycopg.rows").rows
        migration_connection = psycopg.connect(settings.postgres_dsn)
        try:
            apply_postgres_migrations(migration_connection)
        finally:
            migration_connection.close()
        connection = psycopg.connect(settings.postgres_dsn, row_factory=rows.dict_row)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return

    connection = connect(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()
