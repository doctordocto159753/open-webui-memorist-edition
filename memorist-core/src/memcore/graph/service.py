from __future__ import annotations

import importlib
from typing import Any

from memcore.config import Settings
from memcore.graph.falkordb import FalkorDBClient
from memcore.graph.projector import FalkorGraphProjector, GraphProjectionResult


class GraphProjectionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        if self.settings.graph_backend != "falkordb":
            return {
                "enabled": False,
                "status": "disabled",
                "canonical_source": self.settings.canonical_store,
                "source_of_truth": False,
                "projection_version": FalkorGraphProjector.projection_version,
            }
        client = FalkorDBClient(self.settings.falkordb_url or "")
        health = client.health()
        return {
            "enabled": True,
            "status": "ok" if health.ok else "degraded",
            "canonical_source": self.settings.canonical_store,
            "source_of_truth": False,
            "projection_version": FalkorGraphProjector.projection_version,
            "error_sanitized": health.error_sanitized,
        }

    def diagnostics(self) -> dict[str, Any]:
        status = self.status()
        status.update(
            {
                "graph_backend": self.settings.graph_backend,
                "fallback": "postgres_retrieval" if status["status"] == "degraded" else None,
                "outbox_lag": self._outbox_lag()
                if self.settings.canonical_store == "postgres"
                else None,
                "drift_detectable": True,
            }
        )
        return status

    def project_pending(self) -> GraphProjectionResult:
        if self.settings.graph_backend != "falkordb":
            return GraphProjectionResult(degraded=True, last_error_sanitized="graph disabled")
        status = self.status()
        if status["status"] != "ok":
            return GraphProjectionResult(
                degraded=True,
                last_error_sanitized=status["error_sanitized"],
            )
        if self.settings.canonical_store != "postgres" or not self.settings.postgres_dsn:
            return GraphProjectionResult(projected=0)

        result = self._project_pending_postgres()
        return result

    def rebuild(self) -> dict[str, Any]:
        if self.settings.canonical_store != "postgres":
            return {
                "status": "unsupported",
                "reason": "graph rebuild requires PostgreSQL canonical store",
            }
        if not self.settings.postgres_dsn:
            return {
                "status": "failed",
                "reason": "postgres_dsn is required for graph rebuild",
            }
        if self.settings.graph_backend != "falkordb":
            return {
                "status": "degraded",
                "reason": "graph_backend is not falkordb",
                "source": "postgres",
                "projection_version": FalkorGraphProjector.projection_version,
            }
        client = FalkorDBClient(self.settings.falkordb_url or "")
        health = client.health()
        if not health.ok:
            return {
                "status": "degraded",
                "source": "postgres",
                "projection_version": FalkorGraphProjector.projection_version,
                "projected": 0,
                "error_sanitized": health.error_sanitized,
            }

        # A rebuild is replacement, not an additive replay. Clearing the
        # projection first guarantees quarantined/erased PostgreSQL rows cannot
        # survive as stale FalkorDB nodes. An absent graph is safely idempotent.
        try:
            client.delete_graph()
        except Exception as error:
            return {
                "status": "failed",
                "source": "postgres",
                "projection_version": FalkorGraphProjector.projection_version,
                "projected": 0,
                "error_sanitized": str(error)[:240],
            }

        psycopg = importlib.import_module("psycopg")
        projector = FalkorGraphProjector(client)
        connection = psycopg.connect(self.settings.postgres_dsn)
        projected = 0
        skipped = 0
        try:
            with connection.cursor() as cursor:
                cursor.execute(_ACTIVE_MEMORY_GRAPH_ROWS_SQL)
                rows = _rows_to_dicts(cursor)
                for row in rows:
                    is_active = (
                        row.get("memory_status") == "active"
                        and row.get("version_status") == "current"
                    )
                    if not is_active:
                        skipped += 1
                        continue
                    projector.project_full_memory_record(row)
                    projected += 1
                    cursor.execute(
                        """
                        INSERT INTO graph_projection_state (
                            projection_state_uuid, source_uuid, source_type,
                            projection_version, graph_key, status, last_projected_at
                        )
                        VALUES (
                            %s, %s, 'memory_version', %s, %s,
                            'projected', now()
                        )
                        ON CONFLICT (source_type, source_uuid, projection_version) DO UPDATE
                        SET status = 'projected',
                            last_projected_at = now(),
                            last_error_sanitized = NULL,
                            updated_at = now()
                        """,
                        (
                            f"projection-state:{row['memory_version_uuid']}",
                            row["memory_version_uuid"],
                            FalkorGraphProjector.projection_version,
                            f"MemoryVersion:{row['memory_version_uuid']}",
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE graph_projection_outbox
                        SET status = 'projected',
                            last_error_sanitized = NULL,
                            updated_at = now()
                        WHERE source_type = 'memory'
                          AND source_uuid = %s
                          AND status IN ('pending', 'retry')
                        """,
                        (row["memory_uuid"],),
                    )
            connection.commit()
        except Exception as error:
            connection.rollback()
            return {
                "status": "failed",
                "source": "postgres",
                "projection_version": FalkorGraphProjector.projection_version,
                "projected": projected,
                "error_sanitized": str(error)[:240],
            }
        finally:
            connection.close()
        return {
            "status": "ok",
            "source": "postgres",
            "projection_version": FalkorGraphProjector.projection_version,
            "projected": projected,
            "skipped": skipped,
        }

    def _project_pending_postgres(self) -> GraphProjectionResult:
        rebuild = self.rebuild()
        if rebuild.get("status") != "ok":
            return GraphProjectionResult(
                projected=int(rebuild.get("projected") or 0),
                failed=1,
                degraded=rebuild.get("status") == "degraded",
                last_error_sanitized=str(
                    rebuild.get("error_sanitized")
                    or rebuild.get("reason")
                    or "graph projection failed"
                )[:240],
            )
        return GraphProjectionResult(projected=int(rebuild.get("projected") or 0))

    def _outbox_lag(self) -> int | None:
        if not self.settings.postgres_dsn:
            return None
        try:
            psycopg = importlib.import_module("psycopg")
            connection = psycopg.connect(self.settings.postgres_dsn)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT count(*)
                        FROM graph_projection_outbox
                        WHERE status IN ('pending', 'retry')
                        """
                    )
                    row = cursor.fetchone()
                    return int(row[0]) if row else 0
            finally:
                connection.close()
        except Exception:
            return None


_ACTIVE_MEMORY_GRAPH_ROWS_SQL = """
SELECT
    COALESCE(s.workspace_uuid, p.workspace_uuid, 'unknown-workspace') AS workspace_uuid,
    COALESCE(s.project_uuid, 'unknown-project') AS project_uuid,
    s.session_uuid,
    msg.message_uuid,
    tu.text_unit_uuid AS unit_uuid,
    COALESCE(jsa.annotation_uuid, 'no-annotation:' || tu.text_unit_uuid) AS annotation_uuid,
    COALESCE(msr.route_uuid, 'no-route:' || mc.candidate_uuid) AS route_uuid,
    mc.candidate_uuid,
    mem.memory_uuid,
    mv.memory_version_uuid,
    COALESCE(jsa.dominant_function, 'memory') AS dominant_function,
    jsa.receiver_value,
    jsa.context_value,
    jsa.code_value,
    mem.status AS memory_status,
    mv.status AS version_status,
    mv.value AS memory_value
FROM memories mem
JOIN memory_versions mv
    ON mv.memory_version_uuid = mem.current_version_uuid
JOIN memory_evidence_links mel
    ON mel.memory_uuid = mem.memory_uuid
    AND mel.memory_version_uuid = mv.memory_version_uuid
JOIN memory_candidates mc
    ON mc.candidate_uuid = mel.candidate_uuid
JOIN candidate_evidence ce
    ON ce.evidence_uuid = mel.evidence_uuid
JOIN messages msg
    ON msg.message_uuid = ce.message_uuid
JOIN sessions s
    ON s.session_uuid = msg.session_uuid
LEFT JOIN projects p
    ON p.project_uuid = s.project_uuid
JOIN text_units tu
    ON tu.text_unit_uuid = ce.text_unit_uuid
LEFT JOIN jakobson_sentence_annotations jsa
    ON jsa.annotation_uuid = ce.annotation_uuid
LEFT JOIN memory_signal_routes msr
    ON msr.route_uuid = ce.route_uuid
WHERE mem.status = 'active'
  AND mv.status = 'current'
  AND msg.is_deleted = false
  AND msg.visibility = 'visible'
ORDER BY mem.memory_uuid, mv.version_number DESC
"""


def _rows_to_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    description = getattr(cursor, "description", None)
    if description is None:
        return []
    columns = [column[0] for column in description]
    return [dict(zip(columns, row, strict=False)) for row in rows]
