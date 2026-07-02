import re

from memcore.models import (
    ExpandedQuery,
    QueryIntent,
    RetrievalMode,
    RetrievalPlan,
    RetrievalQueryType,
    RetrievalScope,
    RetrievalScopeType,
    Session,
    TemporalFilter,
)

PLANNER_VERSION = "deterministic-planner-v1"
STOP_WORDS = {
    "the",
    "a",
    "an",
    "what",
    "which",
    "did",
    "do",
    "we",
    "i",
    "my",
    "is",
    "are",
    "was",
    "were",
    "to",
    "for",
    "از",
    "به",
    "در",
    "که",
    "چه",
}


class DeterministicRetrievalPlanner:
    def plan(
        self,
        query: str,
        session: Session,
        retrieval_mode: RetrievalMode | str,
        token_budget: int,
        max_expansions: int = 4,
    ) -> RetrievalPlan:
        mode = RetrievalMode(retrieval_mode)
        terms = _meaningful_terms(query)
        intents = _intents(query)
        temporal_filter = _temporal_filter(query)
        scopes = [
            RetrievalScope(
                scope_type=RetrievalScopeType.SESSION,
                scope_uuid=session.session_uuid,
            )
        ]
        if session.project_uuid:
            scopes.append(
                RetrievalScope(
                    scope_type=RetrievalScopeType.PROJECT,
                    scope_uuid=session.project_uuid,
                )
            )
        if session.workspace_uuid:
            scopes.append(
                RetrievalScope(
                    scope_type=RetrievalScopeType.WORKSPACE,
                    scope_uuid=session.workspace_uuid,
                )
            )
        expanded_queries = [
            ExpandedQuery(
                query_text=query,
                query_type=RetrievalQueryType.ORIGINAL,
                generated=False,
                weight=1.0,
            )
        ]
        for term in terms[: max(0, max_expansions - 1)]:
            expanded_queries.append(
                ExpandedQuery(query_text=term, query_type=RetrievalQueryType.EXPANDED, weight=0.5)
            )
        return RetrievalPlan(
            original_query=query,
            retrieval_mode=mode,
            query_intents=intents,
            expanded_queries=expanded_queries,
            scopes=scopes,
            memory_types=_memory_types_for_intents(intents),
            focal_entities=_entities(query),
            temporal_filter=temporal_filter,
            include_conflicts=(
                QueryIntent.CORRECTION_OR_UPDATE in intents or "conflict" in query.lower()
            ),
            token_budget=token_budget,
            abstain_if_insufficient=True,
        )


def _meaningful_terms(query: str) -> list[str]:
    tokens = re.findall(r"[\w\u0600-\u06ff][\w\-\u0600-\u06ff]+", query)
    return [token for token in tokens if token.lower() not in STOP_WORDS]


def _entities(query: str) -> list[str]:
    entities = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", query)
    entities.extend(re.findall(r"\b[A-Z]{2,}\b", query))
    return sorted(set(entities))


def _intents(query: str) -> list[QueryIntent]:
    lowered = query.lower()
    intents = [QueryIntent.CURRENT_TASK]
    if "prefer" in lowered or "ترجیح" in query:
        intents.append(QueryIntent.PREFERENCE)
    if "decision" in lowered or "decided" in lowered or "تصمیم" in query:
        intents.append(QueryIntent.PAST_DECISION)
    if "constraint" in lowered or "do not" in lowered or "نباید" in query:
        intents.append(QueryIntent.CONSTRAINT)
    if "mean" in lowered or "definition" in lowered or "منظور" in query:
        intents.append(QueryIntent.DEFINITION)
    if "previous" in lowered or "before" in lowered or "قبلا" in query or "قبل" in query:
        intents.append(QueryIntent.TEMPORAL_QUESTION)
    if "changed" in lowered or "update" in lowered or "correction" in lowered:
        intents.append(QueryIntent.CORRECTION_OR_UPDATE)
    return list(dict.fromkeys(intents))


def _memory_types_for_intents(intents: list[QueryIntent]) -> list[str]:
    mapping = {
        QueryIntent.PREFERENCE: "preference",
        QueryIntent.PAST_DECISION: "decision",
        QueryIntent.CONSTRAINT: "constraint",
        QueryIntent.DEFINITION: "definition",
        QueryIntent.PAST_EPISODE: "episode",
    }
    values = [mapping[intent] for intent in intents if intent in mapping]
    return values or ["preference", "decision", "constraint", "definition", "fact"]


def _temporal_filter(query: str) -> TemporalFilter | None:
    lowered = query.lower()
    if "previous" in lowered or "before" in lowered or "قبلا" in query or "قبل" in query:
        return TemporalFilter(phrase="previous", historical=True)
    match = re.search(r"\b(20\d{2})(?:-(\d{2}))?\b", query)
    if match:
        year = match.group(1)
        month = match.group(2) or "01"
        return TemporalFilter(
            phrase=match.group(0),
            normalized_from=f"{year}-{month}-01T00:00:00Z",
            precision="month" if match.group(2) else "year",
            historical=True,
        )
    return None
