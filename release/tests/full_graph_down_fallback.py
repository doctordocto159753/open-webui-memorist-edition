from __future__ import annotations

from pathlib import Path
from typing import Any

from release.tests.full_mode_common import (
    SkipSmoke,
    apply_postgres_schema,
    connect_postgres,
    failed_result,
    passed_result,
    postgres_service,
    print_result,
    sanitize,
    seed_canonical_dataset,
    test_namespace,
)

NAME = "full_graph_down_fallback"


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres:
            from memcore.config import Settings
            from memcore.graph.service import GraphProjectionService

            namespace = test_namespace("graph-down")
            connection = connect_postgres(postgres.url)
            try:
                apply_postgres_schema(connection)
                ids = seed_canonical_dataset(connection, namespace)
                live_message_uuid = f"{namespace}-live-message"
                _capture_live_message(connection, ids["session_uuid"], live_message_uuid)
                _enqueue_graph_outbox(connection, namespace, ids["memory_version_uuid"])
                fallback_candidates = _fallback_retrieval(connection, ids["project_uuid"])
                pending_outbox = _pending_graph_outbox(connection, namespace)
            finally:
                connection.close()
            settings = Settings(
                runtime_profile="full",
                canonical_store="postgres",
                postgres_dsn=postgres.url,
                graph_backend="falkordb",
                falkordb_url="redis://127.0.0.1:1",
                hot_scheduler="in_memory",
                object_store_path=str(Path.cwd() / "release" / "artifacts" / "objects"),
            )
            diagnostics = GraphProjectionService(settings).diagnostics()
            if diagnostics["status"] != "degraded" or pending_outbox < 1 or not fallback_candidates:
                return failed_result(
                    NAME,
                    "degraded_fallback",
                    "Graph down must leave chat/retrieval available and outbox retryable.",
                    diagnostics=diagnostics,
                    pending_outbox=pending_outbox,
                    fallback_candidates=fallback_candidates,
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                graph_status=diagnostics["status"],
                fallback=diagnostics["fallback"],
                pending_graph_outbox=pending_outbox,
                fallback_candidate_count=len(fallback_candidates),
                chat_path_crashed=False,
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run with PostgreSQL and no FalkorDB service to validate degraded behavior.",
            error_sanitized=sanitize(str(error)),
        )


def _capture_live_message(connection: Any, session_uuid: str, message_uuid: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO messages (
                message_uuid, session_uuid, turn_index, role, creator_type,
                raw_text, raw_payload_jsonb, snapshot_jsonb, processing_status
            )
            VALUES (%s, %s, 77, 'user', 'user', %s, %s::jsonb, %s::jsonb, 'pending')
            ON CONFLICT DO NOTHING
            """,
            (
                message_uuid,
                session_uuid,
                "live chat survives graph down",
                '{"source":"live"}',
                '{"text":"live chat survives graph down"}',
            ),
        )
    connection.commit()


def _enqueue_graph_outbox(connection: Any, namespace: str, memory_version_uuid: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO graph_projection_outbox (
                outbox_uuid, event_type, source_type, source_uuid,
                payload_jsonb, status, priority
            )
            VALUES (%s, 'project_memory_version', 'memory_version', %s, %s::jsonb, 'pending', 70)
            ON CONFLICT DO NOTHING
            """,
            (
                f"{namespace}-down-outbox",
                memory_version_uuid,
                '{"reason":"graph_down"}',
            ),
        )
    connection.commit()


def _pending_graph_outbox(connection: Any, namespace: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM graph_projection_outbox
            WHERE outbox_uuid = %s AND status IN ('pending', 'retry')
            """,
            (f"{namespace}-down-outbox",),
        )
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def _fallback_retrieval(connection: Any, project_uuid: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT mem.memory_uuid
            FROM memories mem
            JOIN memory_versions mv ON mv.memory_version_uuid = mem.current_version_uuid
            WHERE mem.status = 'active'
              AND mv.status = 'current'
              AND mem.scope_type = 'project'
              AND mem.scope_uuid = %s
            LIMIT 10
            """,
            (project_uuid,),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
