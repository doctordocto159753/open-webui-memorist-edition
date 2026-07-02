from __future__ import annotations

from typing import Any

from release.tests.full_mode_common import (
    SkipSmoke,
    apply_postgres_schema,
    assert_falkor_match,
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

NAME = "full_falkordb_projection_smoke"


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres, falkordb_service(NAME) as falkor:
            from memcore.graph.falkordb import FalkorDBClient
            from memcore.graph.projector import FalkorGraphProjector

            connection = connect_postgres(postgres.url)
            namespace = test_namespace("projection")
            try:
                apply_postgres_schema(connection)
                ids = seed_canonical_dataset(connection, namespace)
                record = projection_record(connection, ids)
                projector = FalkorGraphProjector(FalkorDBClient(falkor.url))
                projector.project_full_memory_record(record)
                projector.project_full_memory_record(record)
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO graph_projection_state (
                            projection_state_uuid, source_uuid, source_type,
                            projection_version, graph_key, status, last_projected_at
                        )
                        VALUES (%s, %s, 'memory_version', %s, %s, 'projected', now())
                        ON CONFLICT (source_type, source_uuid, projection_version) DO UPDATE
                        SET status = 'projected', last_projected_at = now(), updated_at = now()
                        """,
                        (
                            f"projection-state:{ids['memory_version_uuid']}",
                            ids["memory_version_uuid"],
                            FalkorGraphProjector.projection_version,
                            f"MemoryVersion:{ids['memory_version_uuid']}",
                        ),
                    )
                connection.commit()
                projection_state_count = _projection_state_count(connection, ids)
            finally:
                connection.close()

            _assert_projected_graph(falkor.url, ids)
            if projection_state_count < 1:
                return failed_result(
                    NAME,
                    "projection_state",
                    "Projection succeeded but PostgreSQL graph_projection_state was not updated.",
                    projection_state_count=projection_state_count,
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                falkordb_provisioned_by=falkor.provisioned_by,
                projection_idempotent=True,
                projection_state_count=projection_state_count,
                verified_nodes=[
                    "Workspace",
                    "Project",
                    "Session",
                    "Message",
                    "TextUnit",
                    "JakobsonAnnotation",
                    "CommunicativeFunction",
                    "Addressee",
                    "ContextReferent",
                    "MemorySignalRoute",
                    "MemoryCandidate",
                    "Memory",
                    "MemoryVersion",
                ],
                verified_edges=[
                    "IN_PROJECT",
                    "HAS_UNIT",
                    "HAS_JAKOBSON_ANNOTATION",
                    "HAS_DOMINANT_FUNCTION",
                    "ADDRESSES",
                    "REFERS_TO",
                    "ROUTES_TO",
                    "DERIVED_FROM",
                    "HAS_VERSION",
                    "EVIDENCED_BY",
                ],
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run with MEMORIST_TEST_POSTGRES_DSN and MEMORIST_TEST_FALKORDB_URL or Docker.",
            error_sanitized=sanitize(str(error)),
        )


def _projection_state_count(connection: Any, ids: dict[str, str]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM graph_projection_state
            WHERE source_uuid = %s AND status = 'projected'
            """,
            (ids["memory_version_uuid"],),
        )
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def _assert_projected_graph(url: str, ids: dict[str, str]) -> None:
    checks = [
        (
            "Workspace",
            f"MATCH (n:Workspace {{uuid:'{ids['workspace_uuid']}'}}) RETURN n.uuid",
            ids["workspace_uuid"],
        ),
        ("Project", f"MATCH (n:Project {{uuid:'{ids['project_uuid']}'}}) RETURN n.uuid", ids["project_uuid"]),
        ("Session", f"MATCH (n:Session {{uuid:'{ids['session_uuid']}'}}) RETURN n.uuid", ids["session_uuid"]),
        ("Message", f"MATCH (n:Message {{uuid:'{ids['message_uuid']}'}}) RETURN n.uuid", ids["message_uuid"]),
        ("TextUnit", f"MATCH (n:TextUnit {{uuid:'{ids['text_unit_uuid']}'}}) RETURN n.uuid", ids["text_unit_uuid"]),
        (
            "JakobsonAnnotation",
            f"MATCH (n:JakobsonAnnotation {{uuid:'{ids['annotation_uuid']}'}}) RETURN n.uuid",
            ids["annotation_uuid"],
        ),
        ("Memory", f"MATCH (n:Memory {{uuid:'{ids['memory_uuid']}'}}) RETURN n.uuid", ids["memory_uuid"]),
        (
            "MemoryVersion",
            f"MATCH (n:MemoryVersion {{uuid:'{ids['memory_version_uuid']}'}}) RETURN n.uuid",
            ids["memory_version_uuid"],
        ),
        (
            "IN_PROJECT",
            (
                f"MATCH (:Workspace {{uuid:'{ids['workspace_uuid']}'}})"
                f"-[:IN_PROJECT]->(:Project {{uuid:'{ids['project_uuid']}'}}) RETURN 'IN_PROJECT'"
            ),
            "IN_PROJECT",
        ),
        (
            "HAS_UNIT",
            (
                f"MATCH (:Message {{uuid:'{ids['message_uuid']}'}})"
                f"-[:HAS_UNIT]->(:TextUnit {{uuid:'{ids['text_unit_uuid']}'}}) RETURN 'HAS_UNIT'"
            ),
            "HAS_UNIT",
        ),
        (
            "HAS_JAKOBSON_ANNOTATION",
            (
                f"MATCH (:TextUnit {{uuid:'{ids['text_unit_uuid']}'}})"
                f"-[:HAS_JAKOBSON_ANNOTATION]->"
                f"(:JakobsonAnnotation {{uuid:'{ids['annotation_uuid']}'}}) "
                "RETURN 'HAS_JAKOBSON_ANNOTATION'"
            ),
            "HAS_JAKOBSON_ANNOTATION",
        ),
        (
            "HAS_VERSION",
            (
                f"MATCH (:Memory {{uuid:'{ids['memory_uuid']}'}})"
                f"-[:HAS_VERSION]->(:MemoryVersion {{uuid:'{ids['memory_version_uuid']}'}}) "
                "RETURN 'HAS_VERSION'"
            ),
            "HAS_VERSION",
        ),
    ]
    for _name, query, expected in checks:
        assert_falkor_match(url, query, expected)


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
