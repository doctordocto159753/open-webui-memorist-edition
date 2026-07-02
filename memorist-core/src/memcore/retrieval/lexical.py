import sqlite3
from typing import Any

from memcore.models import RetrievalPlan, RetrievalScopeType


def create_index(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_version_fts USING fts5(
            memory_version_uuid UNINDEXED,
            memory_uuid UNINDEXED,
            canonical_key,
            memory_type,
            scope_type,
            scope_uuid UNINDEXED,
            subject_predicate,
            normalized_text,
            index_text
        )
        """
    )
    connection.commit()


def rebuild_index(connection: sqlite3.Connection) -> None:
    create_index(connection)
    connection.execute("DELETE FROM memory_version_fts")
    rows = connection.execute(
        """
        SELECT mv.memory_version_uuid,
               mv.memory_uuid,
               m.canonical_key,
               m.memory_type,
               m.scope_type,
               m.scope_uuid,
               mv.normalized_text,
               COALESCE(m.canonical_key, '') || ' ' ||
               COALESCE(m.memory_type, '') || ' ' ||
               COALESCE(m.scope_type, '') || ' ' ||
               COALESCE(mv.normalized_text, '') AS index_text
        FROM memory_versions mv
        JOIN memories m ON m.memory_uuid = mv.memory_uuid
        WHERE m.current_version_uuid = mv.memory_version_uuid
           OR mv.transaction_until IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT INTO memory_version_fts (
                memory_version_uuid,
                memory_uuid,
                canonical_key,
                memory_type,
                scope_type,
                scope_uuid,
                subject_predicate,
                normalized_text,
                index_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["memory_version_uuid"],
                row["memory_uuid"],
                row["canonical_key"],
                row["memory_type"],
                row["scope_type"],
                row["scope_uuid"],
                row["canonical_key"],
                row["normalized_text"],
                row["index_text"],
            ),
        )
    connection.commit()


def verify_consistency(connection: sqlite3.Connection) -> bool:
    authoritative = connection.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0]
    indexed = connection.execute("SELECT COUNT(*) FROM memory_version_fts").fetchone()[0]
    return int(authoritative) == int(indexed)


class LexicalGenerator:
    def __init__(self, connection: sqlite3.Connection, top_k: int = 20) -> None:
        self.connection = connection
        self.top_k = top_k

    def generate(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        rebuild_index(self.connection)
        if not plan.expanded_queries:
            return []
        query = " OR ".join(
            _escape_fts(query.query_text) for query in plan.expanded_queries if query.query_text
        )
        if not query.strip():
            return []
        scope_clause, scope_params = _scope_clause(plan)
        rows = self.connection.execute(
            f"""
            SELECT fts.memory_uuid,
                   fts.memory_version_uuid,
                   bm25(memory_version_fts) AS lexical_score,
                   m.memory_type,
                   m.scope_type,
                   m.scope_uuid
            FROM memory_version_fts fts
            JOIN memories m ON m.memory_uuid = fts.memory_uuid
            JOIN memory_versions mv ON mv.memory_version_uuid = fts.memory_version_uuid
            WHERE memory_version_fts MATCH ?
              {scope_clause}
            ORDER BY bm25(memory_version_fts)
            LIMIT ?
            """,
            (query, *scope_params, self.top_k),
        ).fetchall()
        return [
            {
                "generator_type": "fts5_bm25",
                "memory_uuid": row["memory_uuid"],
                "memory_version_uuid": row["memory_version_uuid"],
                "rank": index + 1,
                "lexical_score": float(row["lexical_score"]),
            }
            for index, row in enumerate(rows)
        ]


def _escape_fts(query: str) -> str:
    terms = [term.replace('"', "") for term in query.split() if term.strip()]
    return " ".join(f'"{term}"' for term in terms)


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
