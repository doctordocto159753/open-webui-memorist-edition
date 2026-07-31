from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any

from memcore.models import ScoredMemoryItem


class MessageEvidenceRetriever:
    """Execute a model-proposed plan against scoped canonical message semantics."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def retrieve(
        self,
        *,
        session_uuid: str,
        input_message_uuid: str,
        query_understanding: Mapping[str, Any] | None,
        limit: int = 8,
    ) -> list[ScoredMemoryItem]:
        if not query_understanding:
            return []
        scope = self.connection.execute(
            """
            SELECT session.workspace_uuid, session.project_uuid, actor.user_uuid
            FROM sessions session
            LEFT JOIN memorist_session_actors actor
              ON actor.session_uuid = session.session_uuid
            WHERE session.session_uuid = ?
            """,
            (session_uuid,),
        ).fetchone()
        if scope is None:
            return []
        requested_labels = {
            _normalize(value)
            for value in [
                query_understanding.get("primary_topic"),
                query_understanding.get("secondary_topic"),
                query_understanding.get("process_label"),
                *(query_understanding.get("entities") or []),
            ]
            if isinstance(value, str) and value.strip()
        }
        stage_ordinal = query_understanding.get("stage_ordinal")
        rows = self.connection.execute(
            """
            SELECT analysis.semantic_analysis_uuid, analysis.message_uuid,
                   analysis.message_version_uuid, analysis.one_line_summary,
                   analysis.primary_topic, analysis.secondary_topic,
                   analysis.source_authority, analysis.epistemic_status,
                   analysis.temporal_status, analysis.importance, analysis.created_at,
                   message.raw_text,
                   GROUP_CONCAT(DISTINCT alias.normalized_alias) AS concept_aliases,
                   GROUP_CONCAT(DISTINCT process.process_label) AS process_labels,
                   GROUP_CONCAT(DISTINCT process.stage_ordinal) AS stage_ordinals,
                   GROUP_CONCAT(DISTINCT entity.canonical_name) AS entity_names
            FROM message_semantic_analyses analysis
            JOIN messages message ON message.message_uuid = analysis.message_uuid
            JOIN sessions source_session ON source_session.session_uuid = message.session_uuid
            LEFT JOIN message_concept_tags tag
              ON tag.semantic_analysis_uuid = analysis.semantic_analysis_uuid
            LEFT JOIN concept_aliases alias ON alias.concept_uuid = tag.concept_uuid
            LEFT JOIN message_process_references process
              ON process.semantic_analysis_uuid = analysis.semantic_analysis_uuid
            LEFT JOIN message_entity_references entity
              ON entity.semantic_analysis_uuid = analysis.semantic_analysis_uuid
            WHERE analysis.message_uuid <> ?
              AND analysis.status IN ('succeeded', 'partial')
              AND analysis.erased_at IS NULL
              AND message.is_deleted = 0
              AND message.visibility = 'visible'
              AND message.redaction_status = 'none'
              AND source_session.workspace_uuid = ?
              AND (
                source_session.project_uuid = ?
                OR (? IS NULL AND source_session.project_uuid IS NULL)
              )
              AND analysis.user_uuid = ?
              AND analysis.raw_text_hash = message.content_hash
              AND (
                analysis.message_version_uuid IS NULL
                OR analysis.message_version_uuid = (
                  SELECT version.message_version_uuid
                  FROM message_versions version
                  WHERE version.message_uuid = message.message_uuid
                  ORDER BY version.version_number DESC LIMIT 1
                )
              )
            GROUP BY analysis.semantic_analysis_uuid
            ORDER BY analysis.created_at DESC
            LIMIT 100
            """,
            (
                input_message_uuid,
                scope["workspace_uuid"],
                scope["project_uuid"],
                scope["project_uuid"],
                scope["user_uuid"],
            ),
        ).fetchall()
        ranked: list[tuple[float, Any]] = []
        for row in rows:
            labels = {
                _normalize(value)
                for value in [
                    row["primary_topic"],
                    row["secondary_topic"],
                    *_split_group(row["concept_aliases"]),
                    *_split_group(row["process_labels"]),
                    *_split_group(row["entity_names"]),
                ]
                if value
            }
            label_matches = len(requested_labels & labels)
            row_stages = {int(value) for value in _split_group(row["stage_ordinals"]) if value}
            stage_match = stage_ordinal is not None and int(stage_ordinal) in row_stages
            if not label_matches and not stage_match:
                continue
            score = min(0.96, 0.48 + (0.12 * label_matches) + (0.24 if stage_match else 0))
            score += min(0.05, float(row["importance"] or 0) * 0.05)
            ranked.append((score, row))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [_to_scored(row, score) for score, row in ranked[:limit]]


def _to_scored(row: Any, score: float) -> ScoredMemoryItem:
    current = str(row["temporal_status"]) not in {
        "historical",
        "satisfied",
        "cancelled",
        "superseded",
        "expired",
        "stale",
    }
    return ScoredMemoryItem(
        memory_uuid=f"message:{row['message_uuid']}",
        memory_version_uuid=f"message-version:{row['message_version_uuid'] or row['message_uuid']}",
        memory_type="message_evidence",
        scope_type="project",
        normalized_text=str(row["one_line_summary"] or "message evidence"),
        current=current,
        valid_time_label=str(row["temporal_status"] or "unknown"),
        confidence_label=str(row["epistemic_status"] or "unknown"),
        source_authority_label=str(row["source_authority"]),
        evidence_text=str(row["raw_text"] or ""),
        final_score=score,
        debug_score_trace={"message_graph_score": score, "source": "message_semantics"},
    )


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _split_group(value: Any) -> list[str]:
    return str(value).split(",") if value else []
