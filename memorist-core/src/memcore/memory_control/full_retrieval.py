from __future__ import annotations

import json
import re
from hashlib import sha256
from time import perf_counter
from typing import Any

from memcore.attachments.budget import budget_for_mode, compute_attachment_budget
from memcore.attachments.builder import AttachmentBuilder
from memcore.attachments.renderer import render_attachment
from memcore.config import Settings
from memcore.graph.falkordb import FalkorDBClient
from memcore.memory_control.policy import MemoristTurnPolicy
from memcore.memory_control.repository import MemoryControlRepository
from memcore.models import PreflightResponse, PreflightStatus, ScoredMemoryItem, new_uuid, utc_now
from memcore.preflight import PreflightRequest
from memcore.validators.ijson import dump_ijson


class FullPostgresPreflightService:
    def __init__(self, connection: Any, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.control = MemoryControlRepository(connection, settings.runtime_profile)

    def run(
        self,
        request: PreflightRequest,
        policy: MemoristTurnPolicy,
        *,
        attachment_review: bool,
    ) -> PreflightResponse:
        started = perf_counter()
        if not policy.recall_enabled or not policy.attachment_enabled:
            return PreflightResponse(
                status=PreflightStatus.DISABLED,
                latency_ms=0,
                attachment_mode="disabled",
                budget_reason="turn policy disables Memorist recall",
                attachment_review=attachment_review,
            )
        scope = self._scope(request)
        budget = compute_attachment_budget(
            mode="full",
            requested_budget=request.token_budget,
            target_model=request.target_model,
            model_context_window=request.model_context_window,
            recent_conversation_text=request.recent_conversation_text,
            estimated_recent_conversation_tokens=request.estimated_recent_conversation_tokens,
            max_tokens=self.settings.attachment_max_tokens,
            context_ratio=self.settings.attachment_context_ratio,
            min_tokens=self.settings.attachment_min_tokens,
            reserved_completion_tokens=self.settings.attachment_reserved_completion_tokens,
            fallback_context_window=self.settings.unknown_model_context_window,
            safety_margin_tokens=self.settings.attachment_safety_margin_tokens,
        )
        run_uuid = new_uuid()
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO retrieval_runs (
                retrieval_run_uuid, session_uuid, project_uuid, input_message_uuid,
                retrieval_mode, original_query, token_budget, status, planner_version,
                config_snapshot_ijson, turn_policy, graph_status, created_at, schema_version
            ) VALUES (?, ?, ?, ?, 'full', ?, ?, 'running', 'full-postgres-v1', ?, 'full',
                      'pending', ?, 1)
            """,
            (
                run_uuid,
                request.session_uuid,
                scope["project_uuid"],
                request.input_message_uuid,
                scope["raw_text"] or "",
                budget.effective_token_budget,
                dump_ijson({"workspace_uuid": scope["workspace_uuid"], "runtime": "full"}),
                now,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO retrieval_queries (
                retrieval_query_uuid, retrieval_run_uuid, query_index, query_type,
                query_text, weight, filters_ijson, created_at, schema_version
            ) VALUES (?, ?, 0, 'original', ?, 1.0, ?, ?, 1)
            """,
            (
                new_uuid(),
                run_uuid,
                scope["raw_text"] or "",
                dump_ijson({"workspace_uuid": scope["workspace_uuid"]}),
                now,
            ),
        )
        canonical = self._canonical_candidates(scope, limit=30)
        active_blocks = self._active_blocks(scope)
        graph_rows, graph_status, degraded_reason = self._graph_candidates(scope)
        if graph_status == "degraded" and not self.settings.allow_full_graph_degraded:
            self.connection.execute(
                """
                UPDATE retrieval_runs
                SET status = 'failed', graph_status = 'degraded', degraded_reason = ?,
                    finished_at = ?, latency_ms = ?
                WHERE retrieval_run_uuid = ?
                """,
                (
                    degraded_reason or "falkordb_unavailable",
                    utc_now(),
                    int((perf_counter() - started) * 1000),
                    run_uuid,
                ),
            )
            self.connection.commit()
            raise RuntimeError(f"Full graph retrieval unavailable: {degraded_reason or 'unknown'}")
        selected = self._validate_and_merge(scope, canonical, graph_rows)
        attachment_uuid: str | None = None
        rendered: str | None = None
        token_count = 0
        if selected or active_blocks:
            attachment_uuid, rendered, token_count = self._build_attachment(
                request=request,
                scope=scope,
                run_uuid=run_uuid,
                selected=selected,
                token_budget=budget.effective_token_budget,
                attachment_review=attachment_review,
                active_blocks=active_blocks,
            )
        elapsed = int((perf_counter() - started) * 1000)
        has_context = bool(selected or active_blocks)
        status = "completed" if has_context else "abstained"
        self.connection.execute(
            """
            UPDATE retrieval_runs
            SET status = ?, completed_at = ?, finished_at = ?, latency_ms = ?,
                abstention_status = ?, abstention_reason = ?, graph_status = ?,
                degraded_reason = ?
            WHERE retrieval_run_uuid = ?
            """,
            (
                status,
                utc_now(),
                utc_now(),
                elapsed,
                None if has_context else "abstained",
                None if has_context else "no scoped canonical candidates or active blocks",
                graph_status,
                degraded_reason,
                run_uuid,
            ),
        )
        self.control.audit(
            "full_retrieval_completed",
            policy,
            session_uuid=request.session_uuid,
            input_message_uuid=request.input_message_uuid,
            workspace_uuid=str(scope["workspace_uuid"]),
            user_uuid=request.user_uuid,
            attachment_uuid=attachment_uuid,
            degraded_reason=degraded_reason,
            detail={
                "graph_status": graph_status,
                "selected_count": len(selected),
                "active_block_count": len(active_blocks),
            },
        )
        self.connection.commit()
        if not has_context:
            return PreflightResponse(
                status=PreflightStatus.ABSTAINED,
                retrieval_run_uuid=run_uuid,
                abstention_status="abstained",
                latency_ms=elapsed,
                effective_token_budget=budget.effective_token_budget,
                budget_reason=budget.budget_reason,
                token_estimation_method=budget.token_estimation_method,
                attachment_mode="full",
                budget_warnings=budget.warnings,
                attachment_review=attachment_review,
                graph_status=graph_status,
                degraded_reason=degraded_reason,
            )
        return PreflightResponse(
            status=PreflightStatus.ATTACHED,
            attachment_uuid=attachment_uuid,
            retrieval_run_uuid=run_uuid,
            rendered_attachment=rendered,
            token_count=token_count,
            latency_ms=elapsed,
            effective_token_budget=budget.effective_token_budget,
            budget_reason=budget.budget_reason,
            token_estimation_method=budget.token_estimation_method,
            attachment_mode="full",
            budget_warnings=budget.warnings,
            attachment_review=attachment_review,
            graph_status=graph_status,
            degraded_reason=degraded_reason,
        )

    def _scope(self, request: PreflightRequest) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT s.session_uuid, s.workspace_uuid, s.project_uuid, m.raw_text
            FROM sessions s JOIN messages m ON m.session_uuid = s.session_uuid
            WHERE s.session_uuid = ? AND m.message_uuid = ?
            """,
            (request.session_uuid, request.input_message_uuid),
        ).fetchone()
        if row is None:
            raise LookupError("session or input message not found")
        if not request.workspace_uuid:
            raise PermissionError("workspace identity required")
        if str(row["workspace_uuid"]) != request.workspace_uuid:
            raise PermissionError("workspace scope mismatch")
        return dict(row)

    def _canonical_candidates(self, scope: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        terms = _query_terms(str(scope["raw_text"] or ""))
        predicates = " OR ".join("lower(mv.normalized_text) LIKE ?" for _ in terms) or "TRUE"
        params: list[Any] = [f"%{term}%" for term in terms]
        params.extend(
            [
                scope["session_uuid"],
                scope["project_uuid"],
                scope["workspace_uuid"],
                limit,
            ]
        )
        rows = self.connection.execute(
            f"""
            SELECT m.memory_uuid, mv.memory_version_uuid,
                   COALESCE(mc.candidate_type, 'memory') AS memory_type, m.scope_type,
                   m.scope_uuid, mv.normalized_text, mv.confidence, mv.importance,
                   mv.valid_from, mv.valid_until,
                   COALESCE(mc.source_authority, 'unknown') AS source_authority,
                   ce.evidence_uuid, ce.evidence_text
            FROM memories m
            JOIN memory_versions mv ON mv.memory_version_uuid = m.current_version_uuid
            LEFT JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
            LEFT JOIN memory_evidence_links mel
                   ON mel.memory_version_uuid = mv.memory_version_uuid
            LEFT JOIN candidate_evidence ce ON ce.evidence_uuid = mel.evidence_uuid
            WHERE m.status = 'active' AND mv.status = 'current'
              AND ({predicates})
              AND ((m.scope_type = 'session' AND m.scope_uuid = ?)
                OR (m.scope_type = 'project' AND m.scope_uuid = ?)
                OR (m.scope_type = 'workspace' AND m.scope_uuid = ?))
            ORDER BY mv.importance DESC, mv.confidence DESC, mv.created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def _graph_candidates(
        self, scope: dict[str, Any]
    ) -> tuple[list[dict[str, str]], str, str | None]:
        client = FalkorDBClient(self.settings.falkordb_url or "")
        health = client.health()
        if not health.ok:
            return [], "degraded", health.error_sanitized or "falkordb_unavailable"
        try:
            rows = client.query_memory_versions(
                workspace_uuid=str(scope["workspace_uuid"]),
                terms=_query_terms(str(scope["raw_text"] or "")),
                limit=10,
            )
            return rows, "healthy", None
        except Exception as error:
            return [], "degraded", f"graph_query_failed:{type(error).__name__}"

    def _active_blocks(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT b.block_uuid, b.block_type, b.scope_type, b.scope_uuid,
                   bv.block_version_uuid, bv.value, bv.source_snapshot_hash
            FROM memory_blocks b
            JOIN memory_block_versions bv ON bv.block_version_uuid = b.current_version_uuid
            WHERE b.status = 'active'
              AND ((b.scope_type = 'session' AND b.scope_uuid = ?)
                OR (b.scope_type = 'project' AND b.scope_uuid = ?)
                OR (b.scope_type = 'workspace' AND b.scope_uuid = ?))
            ORDER BY b.scope_type, b.block_type, b.block_uuid
            LIMIT 20
            """,
            (scope["session_uuid"], scope["project_uuid"], scope["workspace_uuid"]),
        ).fetchall()
        return [dict(row) for row in rows]

    def _validate_and_merge(
        self,
        scope: dict[str, Any],
        canonical: list[dict[str, Any]],
        graph_rows: list[dict[str, str]],
    ) -> list[ScoredMemoryItem]:
        by_version: dict[str, dict[str, Any]] = {}
        for candidate in canonical:
            version_uuid = str(candidate["memory_version_uuid"])
            row = by_version.setdefault(
                version_uuid,
                {
                    **candidate,
                    "evidence_uuids": [],
                    "evidence_texts": [],
                },
            )
            if candidate.get("evidence_uuid"):
                row["evidence_uuids"].append(str(candidate["evidence_uuid"]))
            if candidate.get("evidence_text"):
                row["evidence_texts"].append(str(candidate["evidence_text"]))
        for graph in graph_rows:
            version_uuid = graph["memory_version_uuid"]
            if version_uuid in by_version:
                if self._valid_graph_source(graph):
                    by_version[version_uuid]["graph"] = True
                    by_version[version_uuid]["graph_fact_uuid"] = graph.get("graph_fact_uuid")
                    by_version[version_uuid]["graph_edge_uuid"] = graph.get("graph_edge_uuid")
                continue
            if not self._valid_graph_source(graph):
                continue
            validated = self.connection.execute(
                """
                SELECT m.memory_uuid, mv.memory_version_uuid, 'memory' AS memory_type,
                       m.scope_type,
                       m.scope_uuid, mv.normalized_text, mv.confidence, mv.importance,
                       mv.valid_from, mv.valid_until, 'graph_projection' AS source_authority,
                       NULL AS evidence_uuid, NULL AS evidence_text
                FROM memories m JOIN memory_versions mv
                  ON mv.memory_version_uuid = m.current_version_uuid
                WHERE m.memory_uuid = ? AND mv.memory_version_uuid = ?
                  AND m.status = 'active' AND mv.status = 'current'
                  AND ((m.scope_type = 'session' AND m.scope_uuid = ?)
                    OR (m.scope_type = 'project' AND m.scope_uuid = ?)
                    OR (m.scope_type = 'workspace' AND m.scope_uuid = ?))
                """,
                (
                    graph["memory_uuid"],
                    version_uuid,
                    scope["session_uuid"],
                    scope["project_uuid"],
                    scope["workspace_uuid"],
                ),
            ).fetchone()
            if validated is not None:
                by_version[version_uuid] = {
                    **dict(validated),
                    "graph": True,
                    "graph_fact_uuid": graph.get("graph_fact_uuid"),
                    "graph_edge_uuid": graph.get("graph_edge_uuid"),
                    "evidence_uuids": [],
                    "evidence_texts": [],
                }
        items: list[ScoredMemoryItem] = []
        for index, row in enumerate(by_version.values()):
            score = max(0.1, 1.0 - index * 0.04) + (0.08 if row.get("graph") else 0)
            items.append(
                ScoredMemoryItem(
                    memory_uuid=str(row["memory_uuid"]),
                    memory_version_uuid=str(row["memory_version_uuid"]),
                    memory_type=str(row["memory_type"]),
                    scope_type=str(row["scope_type"]),
                    scope_uuid=row["scope_uuid"],
                    normalized_text=str(row["normalized_text"]),
                    current=True,
                    valid_time_label="current",
                    confidence_label=_confidence_label(float(row["confidence"])),
                    source_authority_label=str(row["source_authority"]),
                    evidence_text="\n".join(dict.fromkeys(row["evidence_texts"])) or None,
                    final_score=round(score, 6),
                    debug_score_trace={
                        "postgres_canonical": True,
                        "graph": bool(row.get("graph")),
                        "evidence_uuids": list(dict.fromkeys(row["evidence_uuids"])),
                        "graph_fact_uuid": row.get("graph_fact_uuid"),
                        "graph_edge_uuid": row.get("graph_edge_uuid"),
                    },
                )
            )
        return sorted(items, key=lambda item: item.final_score, reverse=True)[:12]

    def _valid_graph_source(self, graph: dict[str, str]) -> bool:
        route_uuid = graph.get("graph_fact_uuid")
        candidate_uuid = graph.get("graph_edge_uuid")
        if not route_uuid or not candidate_uuid:
            return False
        row = self.connection.execute(
            """
            SELECT 1
            FROM memory_evidence_links link
            JOIN candidate_evidence evidence ON evidence.evidence_uuid = link.evidence_uuid
            WHERE link.memory_uuid = ? AND link.memory_version_uuid = ?
              AND link.candidate_uuid = ? AND evidence.candidate_uuid = ?
              AND evidence.route_uuid = ?
            LIMIT 1
            """,
            (
                graph["memory_uuid"],
                graph["memory_version_uuid"],
                candidate_uuid,
                candidate_uuid,
                route_uuid,
            ),
        ).fetchone()
        return row is not None

    def _build_attachment(
        self,
        *,
        request: PreflightRequest,
        scope: dict[str, Any],
        run_uuid: str,
        selected: list[ScoredMemoryItem],
        token_budget: int,
        attachment_review: bool,
        active_blocks: list[dict[str, Any]],
    ) -> tuple[str, str, int]:
        attachment_uuid = new_uuid()
        packet = AttachmentBuilder(self.connection)._packet(
            attachment_uuid,
            run_uuid,
            request.session_uuid,
            request.input_message_uuid,
            "full",
            selected,
            None,
            None,
        )
        packet = packet.model_copy(
            update={
                "current_context": [
                    {
                        "block_uuid": block["block_uuid"],
                        "block_version_uuid": block["block_version_uuid"],
                        "block_type": block["block_type"],
                        "scope": {
                            "scope_type": block["scope_type"],
                            "scope_uuid": block["scope_uuid"],
                        },
                        "memory_uuid": block["block_uuid"],
                        "memory_version_uuid": block["block_version_uuid"],
                        "normalized_text": block["value"],
                        "evidence_text": None,
                    }
                    for block in active_blocks
                ]
                + packet.current_context,
                "provenance": packet.provenance
                + [
                    {
                        "block_uuid": block["block_uuid"],
                        "block_version_uuid": block["block_version_uuid"],
                    }
                    for block in active_blocks
                ],
            }
        )
        rendered, token_count = render_attachment(packet, budget_for_mode("full", token_budget))
        packet_json = packet.model_dump(mode="json")
        ijson = dump_ijson(packet_json)
        now = utc_now()
        graph_source_ids = [
            source_uuid
            for item in selected
            for source_uuid in (
                (item.debug_score_trace or {}).get("graph_fact_uuid"),
                (item.debug_score_trace or {}).get("graph_edge_uuid"),
            )
            if source_uuid
        ]
        self.connection.execute(
            """
            INSERT INTO memory_context_attachments (
                attachment_uuid, session_uuid, project_uuid, input_message_uuid,
                retrieval_run_uuid, attachment_jsonb, token_count, mode,
                attachment_mode, ijson_attachment, rendered_attachment,
                source_memory_uuids_ijson, source_block_uuids_ijson,
                source_fact_edge_uuids_ijson, confidence, owner_user_uuid,
                workspace_uuid, lifecycle_status, attachment_review, generation,
                created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?::jsonb, ?, 'full', 'full', ?, ?, ?, ?, ?, 0.8,
                      ?, ?, 'prepared', ?, 1, ?, 1)
            """,
            (
                attachment_uuid,
                request.session_uuid,
                scope["project_uuid"],
                request.input_message_uuid,
                run_uuid,
                json.dumps(packet_json, ensure_ascii=False, sort_keys=True),
                token_count,
                ijson,
                rendered,
                dump_ijson([item.memory_uuid for item in selected]),
                dump_ijson([block["block_uuid"] for block in active_blocks]),
                dump_ijson(graph_source_ids),
                request.user_uuid,
                scope["workspace_uuid"],
                attachment_review,
                now,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO memorist_attachment_lifecycle_events (
                lifecycle_event_uuid, attachment_uuid, lifecycle_status, idempotency_key,
                user_uuid, workspace_uuid, detail_ijson, created_at, schema_version
            ) VALUES (?, ?, 'prepared', ?, ?, ?, ?, ?, 1)
            """,
            (
                new_uuid(),
                attachment_uuid,
                f"prepared:{attachment_uuid}",
                request.user_uuid,
                scope["workspace_uuid"],
                dump_ijson({"attachment_review": attachment_review}),
                now,
            ),
        )
        for rank, item in enumerate(selected, start=1):
            trace = item.debug_score_trace or {}
            self.connection.execute(
                """
                INSERT INTO retrieval_candidates (
                    retrieval_candidate_uuid, retrieval_run_uuid, memory_uuid,
                    memory_version_uuid, generator_type, generator_rank, graph_score,
                    fused_score, final_score, decision, score_trace_jsonb,
                    score_trace_ijson, source_channel,
                    created_at, schema_version
                ) VALUES (?, ?, ?, ?, 'full_postgres_graph', ?, ?, ?, ?, 'selected',
                          ?::jsonb, ?, 'canonical_postgres', ?, 1)
                """,
                (
                    new_uuid(),
                    run_uuid,
                    item.memory_uuid,
                    item.memory_version_uuid,
                    rank,
                    1.0 if trace.get("graph") else 0.0,
                    item.final_score,
                    item.final_score,
                    json.dumps(trace, ensure_ascii=False, sort_keys=True),
                    dump_ijson(trace),
                    now,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO memorist_retrieval_sources (
                    retrieval_source_uuid, retrieval_run_uuid, attachment_uuid, source_type,
                    source_uuid, memory_uuid, memory_version_uuid, workspace_uuid,
                    provenance_ijson, created_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT (retrieval_run_uuid, source_type, source_uuid) DO NOTHING
                """,
                (
                    new_uuid(),
                    run_uuid,
                    attachment_uuid,
                    "memory_version",
                    item.memory_version_uuid,
                    item.memory_uuid,
                    item.memory_version_uuid,
                    scope["workspace_uuid"],
                    dump_ijson(
                        {
                            "memory_version_uuid": item.memory_version_uuid,
                            "evidence_text_present": bool(item.evidence_text),
                            "evidence_hash": sha256((item.evidence_text or "").encode()).hexdigest()
                            if item.evidence_text
                            else None,
                            "canonical_store": "postgres",
                        }
                    ),
                    now,
                ),
            )
            for evidence_uuid in trace.get("evidence_uuids") or []:
                self._insert_source(
                    run_uuid,
                    attachment_uuid,
                    "source_evidence",
                    str(evidence_uuid),
                    scope,
                    item,
                    now,
                )
            for source_type, key in (
                ("graph_fact", "graph_fact_uuid"),
                ("graph_edge", "graph_edge_uuid"),
            ):
                source_uuid = trace.get(key)
                if source_uuid:
                    self._insert_source(
                        run_uuid,
                        attachment_uuid,
                        source_type,
                        str(source_uuid),
                        scope,
                        item,
                        now,
                    )
        for block in active_blocks:
            self.connection.execute(
                """
                INSERT INTO memorist_retrieval_sources (
                    retrieval_source_uuid, retrieval_run_uuid, attachment_uuid, source_type,
                    source_uuid, workspace_uuid, provenance_ijson, created_at, schema_version
                ) VALUES (?, ?, ?, 'active_block', ?, ?, ?, ?, 1)
                ON CONFLICT (retrieval_run_uuid, source_type, source_uuid) DO NOTHING
                """,
                (
                    new_uuid(),
                    run_uuid,
                    attachment_uuid,
                    block["block_version_uuid"],
                    scope["workspace_uuid"],
                    dump_ijson(
                        {
                            "block_uuid": block["block_uuid"],
                            "block_version_uuid": block["block_version_uuid"],
                            "source_snapshot_hash": block["source_snapshot_hash"],
                            "canonical_store": "postgres",
                        }
                    ),
                    now,
                ),
            )
        return attachment_uuid, rendered, token_count

    def _insert_source(
        self,
        run_uuid: str,
        attachment_uuid: str,
        source_type: str,
        source_uuid: str,
        scope: dict[str, Any],
        item: ScoredMemoryItem,
        now: Any,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO memorist_retrieval_sources (
                retrieval_source_uuid, retrieval_run_uuid, attachment_uuid, source_type,
                source_uuid, memory_uuid, memory_version_uuid, workspace_uuid,
                provenance_ijson, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT (retrieval_run_uuid, source_type, source_uuid) DO NOTHING
            """,
            (
                new_uuid(),
                run_uuid,
                attachment_uuid,
                source_type,
                source_uuid,
                item.memory_uuid,
                item.memory_version_uuid,
                scope["workspace_uuid"],
                dump_ijson({"canonical_store": "postgres", "source_type": source_type}),
                now,
            ),
        )


def _query_terms(query: str) -> list[str]:
    return list(
        dict.fromkeys(
            term.lower()
            for term in re.findall(r"[\w\u0600-\u06ff][\w\-\u0600-\u06ff]+", query)
            if len(term) >= 3
        )
    )[:8]


def _confidence_label(value: float) -> str:
    return "high" if value >= 0.8 else "medium" if value >= 0.5 else "low"
