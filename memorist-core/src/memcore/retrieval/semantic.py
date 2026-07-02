import sqlite3
from collections.abc import Sequence
from hashlib import sha256
from math import sqrt
from typing import Any, Protocol

from memcore.models import MemoryVersionEmbedding, RetrievalPlan, RetrievalScopeType
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
        self.provider = provider or DeterministicEmbeddingProvider()
        self.top_k = top_k
        self.embedding_model = embedding_model
        self.embedding_version = embedding_version

    def generate(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        rows = _scoped_memory_versions(self.connection, plan)
        if not rows:
            return []

        query_vector = self.provider.embed_query(plan.original_query)
        expected_dimension = len(query_vector)
        scored: list[dict[str, Any]] = []
        for row in rows:
            stored_vector = self._embedding_for_row(row, expected_dimension)
            score = compare_embeddings(
                self.embedding_model,
                query_vector,
                self.embedding_model,
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

    def _embedding_for_row(self, row: sqlite3.Row, expected_dimension: int) -> list[float]:
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
            (row["memory_version_uuid"], self.embedding_model, self.embedding_version),
        ).fetchone()
        if existing is not None:
            if int(existing["embedding_dimension"]) != expected_dimension:
                raise ValueError("stored embedding dimension is incompatible")
            if existing["content_hash"] == content_hash:
                return [float(value) for value in load_ijson(existing["embedding_ijson"])]

        vector = self.provider.embed_query(text)
        if len(vector) != expected_dimension:
            raise ValueError("embedding provider returned inconsistent dimensions")
        RetrievalRepository(self.connection).upsert_embedding(
            MemoryVersionEmbedding(
                memory_version_uuid=str(row["memory_version_uuid"]),
                embedding_model=self.embedding_model,
                embedding_dimension=expected_dimension,
                embedding_version=self.embedding_version,
                content_hash=content_hash,
                embedding_ijson=dump_ijson(vector),
            )
        )
        return vector


def _content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


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
