from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memcore.model_control.endpoint import (
    EndpointConfigurationError,
    normalize_openai_endpoint,
)
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.schemas import (
    ModelProfileCreate,
    ModelProfilePatch,
    ProviderType,
)
from memcore.model_control.stage_contracts import (
    deterministic_privacy,
    validate_privacy_result,
)
from memcore.model_control.stage_invocation import StageInvocationRequest, StageInvoker
from memcore.models import ModelRole
from memcore.repositories import ProjectRepository, WorkspaceRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


@pytest.fixture
def orchestration_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "orchestration.sqlite")
    apply_migrations(connection)
    yield connection
    connection.close()


def _profile(
    repository: ModelControlRepository,
    role: ModelRole,
    name: str,
    *,
    setup_idempotency_key: str | None = None,
) -> str:
    profile = repository.create_profile(
        ModelProfileCreate(
            profile_name=name,
            provider_type=ProviderType.DETERMINISTIC,
            provider_name="test deterministic",
            model_name=name,
            role=role,
            endpoint_is_local=True,
            supports_structured_output=True,
            supports_json_mode=True,
            supports_embeddings=role is ModelRole.EMBEDDING,
            embedding_dimension=16 if role is ModelRole.EMBEDDING else None,
            privacy_acknowledged=True,
            setup_idempotency_key=setup_idempotency_key,
        )
    )
    return profile.model_profile_uuid


def test_role_resolution_uses_project_workspace_global_precedence(
    orchestration_connection: sqlite3.Connection,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    workspace = WorkspaceRepository(orchestration_connection).create_workspace("Workspace")
    project = ProjectRepository(orchestration_connection).create_project(
        workspace.workspace_uuid,
        "Project",
    )
    global_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "global")
    workspace_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "workspace")
    project_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "project")
    repository.set_default(ModelRole.MEMORY_EXTRACTION, global_uuid)
    repository.set_default(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid,
        workspace_uuid=workspace.workspace_uuid,
    )
    repository.set_default(
        ModelRole.MEMORY_EXTRACTION,
        project_uuid,
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    resolver = RoleResolutionService(repository)

    project = resolver.resolve(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    workspace = resolver.resolve(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid=workspace.workspace_uuid,
        project_uuid="project-other",
    )
    global_result = resolver.resolve(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid="workspace-other",
    )

    assert (project.model_profile_uuid, project.scope_source) == (project_uuid, "project")
    assert (workspace.model_profile_uuid, workspace.scope_source) == (
        workspace_uuid,
        "workspace",
    )
    assert (global_result.model_profile_uuid, global_result.scope_source) == (
        global_uuid,
        "global",
    )


def test_role_resolution_exposes_inheritance_and_unusable_fallback_reason(
    orchestration_connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    extraction_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "extractor")
    repository.set_default(ModelRole.MEMORY_EXTRACTION, extraction_uuid)
    inherited = RoleResolutionService(repository).resolve(
        ModelRole.HIGH_CONFIDENCE_EXTRACTION
    )

    assert inherited.requested_role is ModelRole.HIGH_CONFIDENCE_EXTRACTION
    assert inherited.effective_role is ModelRole.MEMORY_EXTRACTION
    assert inherited.inheritance_source == "memory_extraction"
    assert inherited.model_profile_uuid == extraction_uuid

    remote = repository.create_profile(
        ModelProfileCreate(
            provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
            model_name="remote",
            role=ModelRole.PRIVACY_SENSITIVITY,
            endpoint_url="https://provider.example/v1",
            endpoint_is_local=False,
            supports_json_mode=True,
            secret_strategy="env_var",
            secret_env_var_name="MEMORIST_MISSING_TEST_SECRET",
            privacy_acknowledged=True,
        )
    )
    repository.set_default(ModelRole.PRIVACY_SENSITIVITY, remote.model_profile_uuid)
    monkeypatch.delenv("MEMORIST_MISSING_TEST_SECRET", raising=False)
    fallback = RoleResolutionService(repository).resolve(ModelRole.PRIVACY_SENSITIVITY)

    assert fallback.model_profile_uuid == extraction_uuid
    assert fallback.inheritance_source == "memory_extraction"

    repository.patch_profile(extraction_uuid, ModelProfilePatch(is_enabled=False))
    disabled_fallback = RoleResolutionService(repository).resolve(
        ModelRole.PRIVACY_SENSITIVITY
    )
    assert disabled_fallback.model_profile_uuid is None
    assert disabled_fallback.scope_source == "built_in_fallback"
    assert disabled_fallback.fallback_reason == "secret_reference_unavailable"
    disabled_inheritance = RoleResolutionService(repository).resolve(
        ModelRole.HIGH_CONFIDENCE_EXTRACTION
    )
    assert disabled_inheritance.fallback_reason == "configured_profile_disabled"


@pytest.mark.parametrize(
    ("endpoint", "expected", "operation_url"),
    [
        (
            "https://provider.example",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/v1",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/v1/chat/completions",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/chat/completions",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
    ],
)
def test_endpoint_normalization_never_duplicates_operation_paths(
    endpoint: str,
    expected: str,
    operation_url: str,
) -> None:
    normalized = normalize_openai_endpoint(endpoint)
    assert normalized.base_url == expected
    assert normalized.operation_url("chat/completions") == operation_url
    assert "/chat/completions/v1/chat/completions" not in operation_url


@pytest.mark.parametrize(
    "endpoint",
    [
        "provider.example/v1",
        "https://user:secret@provider.example/v1",
        "https://provider.example/v1?api_key=secret",
        "https://provider.example/v1#fragment",
    ],
)
def test_endpoint_normalization_rejects_unsafe_values(endpoint: str) -> None:
    with pytest.raises(EndpointConfigurationError):
        normalize_openai_endpoint(endpoint)


def test_setup_profile_and_stage_invocation_are_idempotent_and_audited(
    orchestration_connection: sqlite3.Connection,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    first_uuid = _profile(
        repository,
        ModelRole.PRIVACY_SENSITIVITY,
        "privacy-v1",
        setup_idempotency_key="setup:privacy:test",
    )
    second_uuid = _profile(
        repository,
        ModelRole.PRIVACY_SENSITIVITY,
        "privacy-v2",
        setup_idempotency_key="setup:privacy:test",
    )
    repository.set_default(ModelRole.PRIVACY_SENSITIVITY, second_uuid)
    request = StageInvocationRequest(
        role=ModelRole.PRIVACY_SENSITIVITY,
        stage="privacy_sensitivity",
        source_type="memory_candidate",
        source_uuid="candidate-1",
        prompt_id="memorist.privacy_sensitivity",
        prompt_version="2.0",
        idempotency_key="privacy:candidate-1:test",
        input_payload={
            "candidate_text": "ordinary project context",
            "evidence_text": "ordinary project context",
        },
    )
    invoker = StageInvoker(orchestration_connection, repository)

    first = invoker.invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=deterministic_privacy,
    )
    replay = invoker.invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=deterministic_privacy,
    )

    assert first_uuid == second_uuid
    assert repository.get_profile(first_uuid).model_name == "privacy-v2"  # type: ignore[union-attr]
    assert replay.execution_uuid == first.execution_uuid
    assert replay.idempotent_replay is True
    assert orchestration_connection.execute(
        "SELECT COUNT(*) FROM processing_stage_runs"
    ).fetchone()[0] == 1
    assert orchestration_connection.execute(
        "SELECT COUNT(*) FROM prompt_execution_runs "
        "WHERE prompt_id = 'memorist.privacy_sensitivity'"
    ).fetchone()[0] == 1
    assert orchestration_connection.execute(
        "SELECT COUNT(*) FROM model_usage_events WHERE stage = 'privacy_sensitivity'"
    ).fetchone()[0] == 1
