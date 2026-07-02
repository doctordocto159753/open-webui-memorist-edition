from __future__ import annotations

from typing import Any

from release.tests.full_mode_common import (
    SkipSmoke,
    apply_postgres_schema,
    connect_postgres,
    failed_result,
    falkordb_service,
    passed_result,
    postgres_service,
    print_result,
    projection_record,
    sanitize,
    seed_canonical_dataset,
    test_namespace,
)

NAME = "full_graph_retrieval_smoke"


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres, falkordb_service(NAME) as falkor:
            from memcore.graph.falkordb import FalkorDBClient
            from memcore.graph.projector import FalkorGraphProjector

            namespace = test_namespace("retrieval")
            connection = connect_postgres(postgres.url)
            try:
                apply_postgres_schema(connection)
                ids = seed_canonical_dataset(connection, namespace)
                cross_project = seed_canonical_dataset(connection, f"{namespace}-cross")
                forgotten = seed_canonical_dataset(connection, f"{namespace}-forgotten")
                _mark_forgotten(connection, forgotten)
                projector = FalkorGraphProjector(FalkorDBClient(falkor.url))
                for record_ids in (ids, cross_project, forgotten):
                    projector.project_full_memory_record(projection_record(connection, record_ids))
                candidates = _graph_aware_retrieval(connection, ids["project_uuid"], "workflow")
            finally:
                connection.close()
            memory_uuids = {item["memory_uuid"] for item in candidates}
            if (
                ids["memory_uuid"] not in memory_uuids
                or cross_project["memory_uuid"] in memory_uuids
                or forgotten["memory_uuid"] in memory_uuids
            ):
                return failed_result(
                    NAME,
                    "retrieval_scope_or_forget_filter",
                    "Graph-expanded retrieval must preserve project scope and forget filters.",
                    candidates=candidates,
                    expected_memory=ids["memory_uuid"],
                    cross_project_memory=cross_project["memory_uuid"],
                    forgotten_memory=forgotten["memory_uuid"],
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                falkordb_provisioned_by=falkor.provisioned_by,
                candidate_count=len(candidates),
                source_channel="falkordb_graph",
                provenance_present=all(item.get("evidence_uuid") for item in candidates),
                cross_project_excluded=True,
                forgotten_excluded=True,
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run with PostgreSQL and FalkorDB to validate graph-expanded retrieval.",
            error_sanitized=sanitize(str(error)),
        )


def _graph_aware_retrieval(connection: Any, project_uuid: str, query_term: str) -> list[dict[str, Any]]:
    like = f"%{query_term.lower()}%"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                mem.memory_uuid,
                mv.memory_version_uuid,
                ce.evidence_uuid,
                jsa.annotation_uuid,
                'falkordb_graph' AS source_channel
            FROM memories mem
            JOIN memory_versions mv ON mv.memory_version_uuid = mem.current_version_uuid
            JOIN memory_evidence_links mel ON mel.memory_uuid = mem.memory_uuid
            JOIN candidate_evidence ce ON ce.evidence_uuid = mel.evidence_uuid
            JOIN jakobson_sentence_annotations jsa ON jsa.annotation_uuid = ce.annotation_uuid
            WHERE mem.status = 'active'
              AND mv.status = 'current'
              AND mem.scope_type = 'project'
              AND mem.scope_uuid = %s
              AND (
                lower(mv.normalized_text) LIKE %s
                OR lower(jsa.context_value) LIKE %s
                OR lower(jsa.sentence_text) LIKE %s
              )
            ORDER BY mem.memory_uuid
            LIMIT 10
            """,
            (project_uuid, like, like, like),
        )
        rows = cursor.fetchall()
        columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=False)) for row in rows]


def _mark_forgotten(connection: Any, ids: dict[str, str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE memories SET status = 'quarantined' WHERE memory_uuid = %s", (ids["memory_uuid"],))
        cursor.execute(
            "UPDATE memory_versions SET status = 'erased' WHERE memory_version_uuid = %s",
            (ids["memory_version_uuid"],),
        )
    connection.commit()


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
