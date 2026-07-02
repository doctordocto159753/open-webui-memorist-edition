import sqlite3
from typing import Any

from memcore.models import RetrievalPlan


class GraphCandidateGenerator:
    def __init__(
        self,
        connection: sqlite3.Connection | None = None,
        enabled: bool = False,
        top_k: int = 10,
        max_depth: int = 2,
    ) -> None:
        self.connection = connection
        self.enabled = enabled
        self.top_k = top_k
        self.max_depth = max_depth

    def generate(self, plan: RetrievalPlan) -> list[dict[str, Any]]:
        if not self.enabled or self.max_depth < 1 or self.connection is None:
            return []
        terms = plan.focal_entities or _query_terms(plan.original_query)
        if not terms:
            return []
        predicates = []
        params: list[Any] = []
        for term in terms[:5]:
            like = f"%{term.lower()}%"
            predicates.append(
                """
                lower(COALESCE(jsa.receiver_value, '')) LIKE ?
                OR lower(COALESCE(jsa.context_value, '')) LIKE ?
                OR lower(COALESCE(jsa.code_value, '')) LIKE ?
                OR lower(jsa.sentence_text) LIKE ?
                """
            )
            params.extend([like, like, like, like])
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT m.memory_uuid,
                   mv.memory_version_uuid,
                   jsa.dominant_function,
                   jsa.annotation_uuid
            FROM jakobson_sentence_annotations jsa
            JOIN candidate_evidence ce ON ce.annotation_uuid = jsa.annotation_uuid
            JOIN memory_evidence_links mel ON mel.evidence_uuid = ce.evidence_uuid
            JOIN memories m ON m.memory_uuid = mel.memory_uuid
            JOIN memory_versions mv ON mv.memory_version_uuid = m.current_version_uuid
            WHERE m.status = 'active'
              AND ({' OR '.join(f'({predicate})' for predicate in predicates)})
            ORDER BY jsa.dominant_function, m.memory_uuid
            LIMIT ?
            """,
            (*params, self.top_k),
        ).fetchall()
        return [
            {
                "generator_type": "falkordb_graph",
                "source_channel": "falkordb_graph",
                "memory_uuid": row["memory_uuid"],
                "memory_version_uuid": row["memory_version_uuid"],
                "rank": index + 1,
                "graph_score": max(0.1, 1.0 - index * 0.05),
                "score_trace": [
                    {
                        "channel": "falkordb_graph",
                        "dominant_function": row["dominant_function"],
                        "annotation_uuid": row["annotation_uuid"],
                    }
                ],
            }
            for index, row in enumerate(rows)
        ]


def _query_terms(query: str) -> list[str]:
    return [term for term in query.replace("/", " ").split() if len(term) >= 3][:5]
