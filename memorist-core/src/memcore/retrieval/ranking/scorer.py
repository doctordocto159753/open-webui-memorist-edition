import re
import sqlite3

from memcore.models import RetrievalCandidate, RetrievalPlan, ScoredMemoryItem
from memcore.retrieval.ranking.conflicts import conflict_penalty
from memcore.retrieval.ranking.temporal import temporal_score


class DeterministicScorer:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def score(self, candidate: RetrievalCandidate, plan: RetrievalPlan) -> ScoredMemoryItem | None:
        row = self.connection.execute(
            """
            SELECT m.memory_uuid,
                   m.memory_type,
                   m.scope_type,
                   m.scope_uuid,
                   m.current_version_uuid,
                   mv.memory_version_uuid,
                   mv.normalized_text,
                   mv.confidence,
                   mv.importance,
                   mv.valid_from,
                   mv.valid_until,
                   mc.source_authority,
                   ce.evidence_text
            FROM memories m
            JOIN memory_versions mv ON mv.memory_version_uuid = ?
            LEFT JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
            LEFT JOIN memory_evidence_links mel ON mel.memory_version_uuid = mv.memory_version_uuid
            LEFT JOIN candidate_evidence ce ON ce.evidence_uuid = mel.evidence_uuid
            WHERE m.memory_uuid = ?
            LIMIT 1
            """,
            (candidate.memory_version_uuid, candidate.memory_uuid),
        ).fetchone()
        if row is None:
            return None

        current = row["current_version_uuid"] == row["memory_version_uuid"]
        if plan.temporal_filter is None and not current:
            return None
        fused_score = candidate.fused_score or 0.0
        temporal = temporal_score(row["valid_from"], row["valid_until"], plan)
        confidence = float(row["confidence"])
        importance = float(row["importance"])
        authority = _authority_score(row["source_authority"])
        evidence_quality = 1.0 if row["evidence_text"] else 0.4
        query_relevance = _query_relevance(plan.original_query, row["normalized_text"])
        scope_proximity = _scope_proximity(row["scope_type"])
        penalty = conflict_penalty(self.connection, candidate.memory_uuid)
        graph_proximity = candidate.graph_score or 0.0
        usage_recency = 0.5
        sensitivity_penalty = 0.0
        final = (
            fused_score
            + (0.2 * query_relevance)
            + (0.16 * scope_proximity)
            + (0.18 * temporal)
            + (0.14 * authority)
            + (0.16 * confidence)
            + (0.14 * importance)
            + (0.1 * evidence_quality)
            + (0.04 * usage_recency)
            + (0.05 * graph_proximity)
            - penalty
            - sensitivity_penalty
        )
        score_trace = {
            "fused_score": fused_score,
            "query_relevance": query_relevance,
            "scope_proximity": scope_proximity,
            "temporal_score": temporal,
            "authority_score": authority,
            "confidence_score": confidence,
            "importance_score": importance,
            "evidence_quality": evidence_quality,
            "usage_recency": usage_recency,
            "graph_proximity": graph_proximity,
            "conflict_penalty": penalty,
            "sensitivity_penalty": sensitivity_penalty,
        }
        return ScoredMemoryItem(
            memory_uuid=row["memory_uuid"],
            memory_version_uuid=row["memory_version_uuid"],
            memory_type=row["memory_type"],
            scope_type=row["scope_type"],
            scope_uuid=row["scope_uuid"],
            normalized_text=row["normalized_text"],
            current=current,
            valid_time_label=_valid_time_label(row["valid_from"], row["valid_until"], current),
            confidence_label=_confidence_label(confidence),
            source_authority_label=_authority_label(row["source_authority"]),
            evidence_text=row["evidence_text"],
            conflict_status="unresolved" if penalty else "none",
            final_score=round(final, 6),
            debug_score_trace=score_trace,
        )


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _valid_time_label(valid_from: str | None, valid_until: str | None, current: bool) -> str:
    if current and valid_from is None and valid_until is None:
        return "current"
    if valid_until is not None:
        return f"historical until {valid_until}"
    if valid_from is not None:
        return f"current from {valid_from}" if current else f"historical from {valid_from}"
    return "current" if current else "historical"


def _authority_score(source_authority: str | None) -> float:
    if source_authority == "user_explicit":
        return 1.0
    if source_authority == "system_instruction":
        return 0.95
    if source_authority == "tool_observation":
        return 0.8
    if source_authority == "assistant_claim":
        return 0.35
    return 0.55


def _authority_label(source_authority: str | None) -> str:
    if source_authority == "user_explicit":
        return "explicit-user-evidence"
    if source_authority == "system_instruction":
        return "system-policy-evidence"
    if source_authority == "tool_observation":
        return "tool-evidence"
    if source_authority == "assistant_claim":
        return "assistant-claim"
    return "evidence-backed"


def _scope_proximity(scope_type: str) -> float:
    if scope_type == "session":
        return 1.0
    if scope_type == "project":
        return 0.85
    if scope_type == "workspace":
        return 0.65
    return 0.4


def _query_relevance(query: str, normalized_text: str) -> float:
    query_terms = {
        term.lower()
        for term in re.findall(r"[\w\u0600-\u06ff][\w\-\u0600-\u06ff]+", query)
        if len(term) > 2
    }
    if not query_terms:
        return 0.5
    text = normalized_text.lower()
    overlap = sum(1 for term in query_terms if term in text)
    return min(1.0, max(0.25, overlap / len(query_terms)))
