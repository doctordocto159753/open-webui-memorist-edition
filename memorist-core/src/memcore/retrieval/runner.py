import sqlite3
from time import perf_counter

from memcore.config import Settings
from memcore.governance.delivery import DeliveryTraceService
from memcore.models import (
    ExpandedQuery,
    RetrievalCandidate,
    RetrievalPlan,
    RetrievalQuery,
    RetrievalQueryType,
    RetrievalRun,
    RetrievalRunStatus,
    ScoredMemoryItem,
    Session,
)
from memcore.repositories import MessageRepository, RepositoryError, SessionRepository
from memcore.repositories.retrieval import RetrievalRepository
from memcore.retrieval.generators import HybridCandidateGenerator
from memcore.retrieval.planner import PLANNER_VERSION, DeterministicRetrievalPlanner
from memcore.retrieval.ranking.reranker import safe_rerank
from memcore.retrieval.ranking.scorer import DeterministicScorer
from memcore.retrieval.ranking.selection import SelectionResult, select_items
from memcore.validators.ijson import dump_ijson


class RetrievalRunner:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.repository = RetrievalRepository(connection)

    def plan(
        self,
        session_uuid: str,
        input_message_uuid: str,
        retrieval_mode: str,
        token_budget: int,
    ) -> tuple[RetrievalRun, RetrievalPlan]:
        session = _required_session(self.connection, session_uuid)
        message = MessageRepository(self.connection).get_message(input_message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {input_message_uuid}")
        plan = DeterministicRetrievalPlanner().plan(
            message.raw_text or "",
            session,
            retrieval_mode,
            token_budget,
        )
        run = RetrievalRun(
            session_uuid=session_uuid,
            project_uuid=session.project_uuid,
            input_message_uuid=input_message_uuid,
            retrieval_mode=plan.retrieval_mode,
            original_query=plan.original_query,
            token_budget=token_budget,
            status=RetrievalRunStatus.PENDING,
            planner_version=PLANNER_VERSION,
            config_snapshot_ijson=dump_ijson(
                {
                    "retrieval_mode": retrieval_mode,
                    "token_budget": token_budget,
                    "scopes": [scope.model_dump(mode="json") for scope in plan.scopes],
                }
            ),
        )
        run = self.repository.create_run(run)
        self.repository.create_queries(
            [
                self._retrieval_query(run.retrieval_run_uuid, index, query, plan)
                for index, query in enumerate(plan.expanded_queries)
            ]
        )
        return run, plan

    def _retrieval_query(
        self,
        retrieval_run_uuid: str,
        index: int,
        query: ExpandedQuery,
        plan: RetrievalPlan,
    ) -> RetrievalQuery:
        return RetrievalQuery(
            retrieval_run_uuid=retrieval_run_uuid,
            query_index=index,
            query_type=query.query_type or RetrievalQueryType.EXPANDED,
            query_text=query.query_text,
            weight=query.weight,
            filters_ijson=dump_ijson(
                {"scopes": [scope.model_dump(mode="json") for scope in plan.scopes]}
            ),
        )

    def run(
        self,
        session_uuid: str,
        input_message_uuid: str,
        retrieval_mode: str,
        token_budget: int,
    ) -> tuple[RetrievalRun, RetrievalPlan, SelectionResult]:
        started = perf_counter()
        run, plan = self.plan(session_uuid, input_message_uuid, retrieval_mode, token_budget)
        self.repository.mark_run_started(run.retrieval_run_uuid)
        generated = HybridCandidateGenerator(self.connection).generate(plan)
        candidates = [
            RetrievalCandidate(
                retrieval_run_uuid=run.retrieval_run_uuid,
                retrieval_query_uuid=None,
                memory_uuid=item["memory_uuid"],
                memory_version_uuid=item["memory_version_uuid"],
                generator_type=item["generator_type"],
                generator_rank=item.get("rank"),
                lexical_score=item.get("lexical_score"),
                semantic_score=item.get("semantic_score"),
                graph_score=item.get("graph_score"),
                fused_score=item.get("fused_score"),
                score_trace_ijson=dump_ijson(item.get("score_trace", [])),
            )
            for item in generated
        ]
        candidates = self.repository.upsert_candidates(candidates)
        scorer = DeterministicScorer(self.connection)
        scored = [
            scored_item
            for candidate in candidates
            if (scored_item := scorer.score(candidate, plan)) is not None
        ]
        scored = safe_rerank(sorted(scored, key=lambda item: item.final_score, reverse=True))
        selection = select_items(scored)
        latency_ms = int((perf_counter() - started) * 1000)
        if selection.abstention_status:
            self.repository.mark_run_abstained(
                run.retrieval_run_uuid,
                selection.abstention_reason or "abstained",
                latency_ms,
            )
        else:
            self.repository.mark_run_completed(run.retrieval_run_uuid, latency_ms)
        self._persist_final_scores(run.retrieval_run_uuid, scored, selection.selected)
        DeliveryTraceService(self.connection).log_retrieval_run_delivery(
            run.retrieval_run_uuid,
            input_message_uuid,
        )
        final_run = self.repository.get_run(run.retrieval_run_uuid)
        if final_run is None:
            raise RepositoryError(f"Retrieval run not found: {run.retrieval_run_uuid}")
        return final_run, plan, selection

    def _persist_final_scores(
        self,
        retrieval_run_uuid: str,
        scored: list[ScoredMemoryItem],
        selected: list[ScoredMemoryItem],
    ) -> None:
        selected_versions = {item.memory_version_uuid for item in selected}
        candidates = self.repository.list_candidates(retrieval_run_uuid)
        score_by_version = {item.memory_version_uuid: item for item in scored}
        for candidate in candidates:
            item = score_by_version.get(candidate.memory_version_uuid)
            if item is None:
                continue
            self.repository.update_candidate_scores(
                candidate.retrieval_candidate_uuid,
                {
                    "temporal_score": item.debug_score_trace["temporal_score"],
                    "authority_score": item.debug_score_trace["authority_score"],
                    "confidence_score": item.debug_score_trace["confidence_score"],
                    "conflict_penalty": item.debug_score_trace["conflict_penalty"],
                    "final_score": item.final_score,
                    "score_trace_ijson": dump_ijson(item.debug_score_trace),
                    "decision": "selected"
                    if item.memory_version_uuid in selected_versions
                    else "candidate",
                },
            )


def _required_session(connection: sqlite3.Connection, session_uuid: str) -> Session:
    session = SessionRepository(connection).get_session(session_uuid)
    if session is None:
        raise RepositoryError(f"Session not found: {session_uuid}")
    return session
