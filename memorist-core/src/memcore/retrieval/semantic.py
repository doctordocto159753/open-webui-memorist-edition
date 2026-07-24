import sqlite3
from collections.abc import Sequence
from hashlib import sha256
from math import sqrt
from typing import Any, Protocol

from memcore.model_control.registry import provider_for_profile
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.schemas import UsageEventCreate
from memcore.models import (
    MemoryVersionEmbedding,
    ModelRole,
    RetrievalPlan,
    RetrievalScopeType,
)
from memcore.repositories.retrieval import RetrievalRepository
from memcore.validators.ijson import dump_ijson, load_ijson


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class DeterministicEmbeddingProvider:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        buckets = [0.0] * 16
        for char in text.lower():
            buckets[ord(char) % len(buckets)] += 1.0
        return buckets


class ModelControlEmbeddingProvider:
    """Retrieval adapter for the effective embedding processing node."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        profile: dict[str, Any],
        *,
        workspace_uuid: str | None,
        project_uuid: str | None,
    ) -> None:
        self.repository = ModelControlRepository(connection)
        self.provider = provider_for_profile(profile)
        self.model_profile_uuid = str(profile["model_profile_uuid"])
        self.provider_type = str(profile["provider_type"])
        self.model_name = str(profile["model_name"])
        self.workspace_uuid = workspace_uuid
        self.project_uuid = project_uuid

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        response = self.provider.embed(texts, timeout_seconds=8.0)
        self._usage(len(response.vectors), response.input_tokens, response.latency_ms)
        return response.vectors

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        if len(vectors) != 1 or not vectors[0]:
            raise ValueError("embedding processing node returned an invalid query vector")
        return vectors[0]

    def _usage(self, count: int, input_tokens: int, latency_ms: int) -> None:
        self.repository.record_usage_event(
            UsageEventCreate(
                role=ModelRole.EMBEDDING,
                stage="retrieval_query_embedding",
                model_profile_uuid=self.model_profile_uuid,
                provider_type=self.provider_type,
                model_name=self.model_name,
                workspace_uuid=self.workspace_uuid,
                project_uuid=self.project_uuid,
                input_tokens=input_tokens,
                embedding_count=count,
                latency_ms=latency_ms,
                status="ok",
            )
        )


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("cannot compare vectors with different dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def compare_embeddings(
    left_model: str,
    left: Sequence[float],
    right_model: str,
    right: Sequence[float],
) -> float:
    if left_model != right_model:
        raise ValueError("cannot compare embeddings from different models")
    return cosine_similarity(left, right)


class SemanticGenerator:
    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: EmbeddingProvider | None = None,
        top_k: int = 20,
        embedding_model: str = "deterministic-char-16",
        embedding_version: str = "1",
    ) -> None:
        self.connection = connection
        self.provider = provider
        self.top_k = top_k
        self.embedding_model = embedding_model
        self.embedding_version = embedding_version

    def generate(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        rows = _scoped_memory_versions(self.connection, plan)
        if not rows:
            return []

        provider, embedding_model = self._effective_provider(plan)
        query_vector = provider.embed_query(plan.original_query)
        expected_dimension = len(query_vector)
        scored: list[dict[str, Any]] = []
        for row in rows:
            stored_vector = self._embedding_for_row(
                row,
                expected_dimension,
                provider,
                embedding_model,
            )
            score = compare_embeddings(
                embedding_model,
                query_vector,
                embedding_model,
                stored_vector,
            )
            scored.append(
                {
                    "generator_type": "semantic",
                    "memory_uuid": row["memory_uuid"],
                    "memory_version_uuid": row["memory_version_uuid"],
                    "semantic_score": round(score, 6),
                }
            )

        ranked = sorted(
            scored,
            key=lambda item: (
                -float(item["semantic_score"]),
                str(item["memory_version_uuid"]),
            ),
        )[: self.top_k]
        for index, item in enumerate(ranked, start=1):
            item["rank"] = index
        return ranked

    def _effective_provider(self, plan: RetrievalPlan) -> tuple[EmbeddingProvider, str]:
        if self.provider is not None:
            return self.provider, self.embedding_model
        workspace_uuid, project_uuid = _scope_ids(plan)
        repository = ModelControlRepository(self.connection)
        resolution = RoleResolutionService(repository).resolve(
            ModelRole.EMBEDDING,
            workspace_uuid=workspace_uuid,
            project_uuid=project_uuid,
        )
        if resolution.model_profile_uuid is None or resolution.provider_type == "disabled":
            return DeterministicEmbeddingProvider(), self.embedding_model
        profile = repository.get_profile(resolution.model_profile_uuid)
        if profile is None:
            return DeterministicEmbeddingProvider(), self.embedding_model
        payload = profile.model_dump(mode="json")
        return (
            ModelControlEmbeddingProvider(
                self.connection,
                payload,
                workspace_uuid=workspace_uuid,
                project_uuid=project_uuid,
            ),
            profile.model_name,
        )

    def _embedding_for_row(
        self,
        row: sqlite3.Row,
        expected_dimension: int,
        provider: EmbeddingProvider,
        embedding_model: str,
    ) -> list[float]:
        text = str(row["normalized_text"])
        content_hash = _content_hash(text)
        existing = self.connection.execute(
            """
            SELECT *
            FROM memory_version_embeddings
            WHERE memory_version_uuid = ?
              AND embedding_model = ?
              AND embedding_version = ?
            """,
            (row["memory_version_uuid"], embedding_model, self.embedding_version),
        ).fetchone()
        if existing is not None:
            if int(existing["embedding_dimension"]) != expected_dimension:
                raise ValueError("stored embedding dimension is incompatible")
            if existing["content_hash"] == content_hash:
                return [float(value) for value in load_ijson(existing["embedding_ijson"])]

        vector = provider.embed_query(text)
        if len(vector) != expected_dimension:
            raise ValueError("embedding provider returned inconsistent dimensions")
        RetrievalRepository(self.connection).upsert_embedding(
            MemoryVersionEmbedding(
                memory_version_uuid=str(row["memory_version_uuid"]),
                embedding_model=embedding_model,
                embedding_dimension=expected_dimension,
                embedding_version=self.embedding_version,
                content_hash=content_hash,
                embedding_ijson=dump_ijson(vector),
            )
        )
        return vector


def _content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _scope_ids(plan: RetrievalPlan) -> tuple[str | None, str | None]:
    workspace_uuid: str | None = None
    project_uuid: str | None = None
    for scope in plan.scopes:
        if scope.scope_type.value == "workspace":
            workspace_uuid = scope.scope_uuid
        elif scope.scope_type.value == "project":
            project_uuid = scope.scope_uuid
    return workspace_uuid, project_uuid


def _scoped_memory_versions(
    connection: sqlite3.Connection,
    plan: RetrievalPlan,
) -> list[sqlite3.Row]:
    scope_clause, scope_params = _scope_clause(plan)
    version_clause = (
        ""
        if plan.temporal_filter is not None
        else "AND mv.memory_version_uuid = m.current_version_uuid"
    )
    return list(
        connection.execute(
            f"""
            SELECT m.memory_uuid,
                   m.canonical_key,
                   m.memory_type,
                   m.scope_type,
                   m.scope_uuid,
                   mv.memory_version_uuid,
                   mv.normalized_text
            FROM memories m
            JOIN memory_versions mv ON mv.memory_uuid = m.memory_uuid
            WHERE m.status = 'active'
              {version_clause}
              {scope_clause}
            """,
            scope_params,
        )
    )


def _scope_clause(plan: RetrievalPlan) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    for scope in plan.scopes:
        if scope.scope_type is RetrievalScopeType.USER_LOCAL:
            continue
        clauses.append("(m.scope_type = ? AND COALESCE(m.scope_uuid, '') = COALESCE(?, ''))")
        params.extend([scope.scope_type.value, scope.scope_uuid])
    if not clauses:
        return "AND 1 = 0", ()
    return f"AND ({' OR '.join(clauses)})", tuple(params)
