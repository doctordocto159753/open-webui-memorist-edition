from __future__ import annotations

from pathlib import Path
from typing import Any

from release.tests.full_mode_common import (
    SkipSmoke,
    apply_postgres_schema,
    assert_falkor_match,
    connect_postgres,
    failed_result,
    falkordb_service,
    falkor_query_text,
    passed_result,
    postgres_service,
    print_result,
    sanitize,
    seed_canonical_dataset,
    test_namespace,
)

NAME = "full_falkordb_rebuild_smoke"


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres, falkordb_service(NAME) as falkor:
            from memcore.config import Settings
            from memcore.graph.falkordb import FalkorDBClient
            from memcore.graph.service import GraphProjectionService

            namespace = test_namespace("rebuild")
            connection = connect_postgres(postgres.url)
            try:
                apply_postgres_schema(connection)
                ids = seed_canonical_dataset(connection, namespace)
                forgotten = seed_canonical_dataset(connection, f"{namespace}-forgotten")
                _mark_forgotten(connection, forgotten)
            finally:
                connection.close()
            client = FalkorDBClient(falkor.url)
            client.delete_graph()
            settings = Settings(
                runtime_profile="full",
                canonical_store="postgres",
                postgres_dsn=postgres.url,
                graph_backend="falkordb",
                falkordb_url=falkor.url,
                hot_scheduler="in_memory",
                object_store_path=str(Path.cwd() / "release" / "artifacts" / "objects"),
            )
            rebuild = GraphProjectionService(settings).rebuild()
            if rebuild["status"] != "ok" or int(rebuild["projected"]) < 1:
                return failed_result(
                    NAME,
                    "graph_rebuild",
                    "Inspect PostgreSQL active memory rows and FalkorDB connectivity.",
                    rebuild=rebuild,
                )
            assert_falkor_match(
                falkor.url,
                f"MATCH (n:Memory {{uuid:'{ids['memory_uuid']}'}}) RETURN n.uuid",
                ids["memory_uuid"],
            )
            forgotten_response = falkor_query_text(
                falkor.url,
                f"MATCH (n:Memory {{uuid:'{forgotten['memory_uuid']}'}}) RETURN n.uuid",
            )
            if forgotten["memory_uuid"] in forgotten_response:
                return failed_result(
                    NAME,
                    "forgotten_resurrected",
                    "Graph rebuild must filter non-active canonical memories.",
                    forgotten_memory_uuid=forgotten["memory_uuid"],
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                falkordb_provisioned_by=falkor.provisioned_by,
                rebuild=rebuild,
                forgotten_restored=False,
                idempotent=True,
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run graph rebuild smoke with disposable PostgreSQL and FalkorDB services.",
            error_sanitized=sanitize(str(error)),
        )


def _mark_forgotten(connection: Any, ids: dict[str, str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE memories SET status = 'quarantined' WHERE memory_uuid = %s",
            (ids["memory_uuid"],),
        )
        cursor.execute(
            "UPDATE memory_versions SET status = 'erased' WHERE memory_version_uuid = %s",
            (ids["memory_version_uuid"],),
        )
    connection.commit()


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
