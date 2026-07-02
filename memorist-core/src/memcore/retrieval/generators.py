import re
import sqlite3
from typing import Any

from memcore.models import RetrievalMode, RetrievalPlan, RetrievalScopeType
from memcore.retrieval.fusion import reciprocal_rank_fusion
from memcore.retrieval.graph import GraphCandidateGenerator
from memcore.retrieval.lexical import LexicalGenerator
from memcore.retrieval.semantic import SemanticGenerator


class CandidateGenerationConfig:
    lexical_top_k = 20
    semantic_top_k = 20
    graph_top_k = 10
    recent_top_k = 10
    fused_candidate_limit = 30
    rrf_k = 60


class HybridCandidateGenerator:
    def __init__(
        self,
        connection: sqlite3.Connection,
        config: CandidateGenerationConfig | None = None,
    ) -> None:
        self.connection = connection
        self.config = config or CandidateGenerationConfig()

    def generate(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        exact = self._exact_key(plan)
        lexical = LexicalGenerator(self.connection, self.config.lexical_top_k).generate(plan)
        semantic = (
            []
            if plan.retrieval_mode is RetrievalMode.LITE
            else SemanticGenerator(self.connection, top_k=self.config.semantic_top_k).generate(plan)
        )
        recent = self._recent_session(plan)
        active_constraints = self._active_constraints(plan)
        graph = GraphCandidateGenerator(
            self.connection,
            enabled=plan.retrieval_mode in {RetrievalMode.FULL, RetrievalMode.DEBUG},
            top_k=self.config.graph_top_k,
            max_depth=2,
        ).generate(plan)
        return reciprocal_rank_fusion(
            [exact, active_constraints, lexical, semantic, recent, graph],
            weights={
                "exact_key": 1.6,
                "active_constraint": 1.5,
                "fts5_bm25": 1.0,
                "semantic": 0.8,
                "recent_session": 0.6,
                "falkordb_graph": 0.8,
            },
            k=self.config.rrf_k,
            limit=self.config.fused_candidate_limit,
        )

    def _exact_key(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        terms = _technical_terms(plan)
        if not terms:
            return []
        scope_clause, scope_params = _scope_clause(plan)
        version_clause = (
            ""
            if plan.temporal_filter is not None
            else "AND mv.memory_version_uuid = m.current_version_uuid"
        )
        term_clause = " OR ".join(
            ["lower(m.canonical_key) LIKE ? OR lower(mv.normalized_text) LIKE ?" for _ in terms]
        )
        term_params: list[str] = []
        for term in terms:
            like_term = f"%{term.lower()}%"
            term_params.extend([like_term, like_term])
        rows = self.connection.execute(
            f"""
            SELECT m.memory_uuid, mv.memory_version_uuid
            FROM memories m
            JOIN memory_versions mv ON mv.memory_uuid = m.memory_uuid
            WHERE m.status = 'active'
              {version_clause}
              {scope_clause}
              AND ({term_clause})
            ORDER BY m.canonical_key, mv.version_number DESC
            LIMIT ?
            """,
            (*scope_params, *term_params, self.config.lexical_top_k),
        ).fetchall()
        return [
            {
                "generator_type": "exact_key",
                "memory_uuid": row["memory_uuid"],
                "memory_version_uuid": row["memory_version_uuid"],
                "rank": index + 1,
            }
            for index, row in enumerate(rows)
        ]

    def _recent_session(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        session_scope = next(
            (scope for scope in plan.scopes if scope.scope_type.value == "session"),
            None,
        )
        if session_scope is None:
            return []
        rows = self.connection.execute(
            """
            SELECT m.memory_uuid, mv.memory_version_uuid
            FROM memories m
            JOIN memory_versions mv ON mv.memory_version_uuid = m.current_version_uuid
            WHERE m.scope_type = 'session' AND m.scope_uuid = ?
            ORDER BY mv.created_at DESC
            LIMIT ?
            """,
            (session_scope.scope_uuid, self.config.recent_top_k),
        ).fetchall()
        return [
            {
                "generator_type": "recent_session",
                "memory_uuid": row["memory_uuid"],
                "memory_version_uuid": row["memory_version_uuid"],
                "rank": index + 1,
            }
            for index, row in enumerate(rows)
        ]

    def _active_constraints(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        scope_clause, scope_params = _scope_clause(plan)
        rows = self.connection.execute(
            f"""
            SELECT m.memory_uuid, mv.memory_version_uuid
            FROM memories m
            JOIN memory_versions mv ON mv.memory_version_uuid = m.current_version_uuid
            WHERE m.status = 'active'
              AND m.memory_type = 'constraint'
              {scope_clause}
            ORDER BY mv.importance DESC, mv.created_at DESC
            LIMIT ?
            """,
            (*scope_params, self.config.recent_top_k),
        ).fetchall()
        return [
            {
                "generator_type": "active_constraint",
                "memory_uuid": row["memory_uuid"],
                "memory_version_uuid": row["memory_version_uuid"],
                "rank": index + 1,
            }
            for index, row in enumerate(rows)
        ]


def _technical_terms(plan: RetrievalPlan) -> list[str]:
    source = " ".join([plan.original_query, *plan.focal_entities])
    terms = re.findall(r"[\w\u0600-\u06ff]+(?:[-_][\w\u0600-\u06ff]+)+|[A-Z0-9]{2,}", source)
    terms.extend(plan.focal_entities)
    return list(dict.fromkeys(term.strip() for term in terms if term.strip()))


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
