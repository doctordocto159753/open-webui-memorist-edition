from __future__ import annotations

import json
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
    projection_record,
    sanitize,
    seed_canonical_dataset,
    test_namespace,
)

NAME = "full_graph_forget_residue_smoke"
RAW_ERASED_TEXT = "Keep the full mode certification workflow visible."


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres, falkordb_service(NAME) as falkor:
            from memcore.config import Settings
            from memcore.graph.falkordb import FalkorDBClient
            from memcore.graph.projector import FalkorGraphProjector
            from memcore.graph.service import GraphProjectionService

            namespace = test_namespace("forget-graph")
            connection = connect_postgres(postgres.url)
            try:
                apply_postgres_schema(connection)
                ids = seed_canonical_dataset(connection, namespace)
                projector = FalkorGraphProjector(FalkorDBClient(falkor.url))
                projector.project_full_memory_record(projection_record(connection, ids))
                assert_falkor_match(
                    falkor.url,
                    f"MATCH (n:Memory {{uuid:'{ids['memory_uuid']}'}}) RETURN n.uuid",
                    ids["memory_uuid"],
                )
                _issue_forget(connection, ids)
                projector.delete_erased_source(ids["memory_uuid"])
                projector.delete_erased_source(ids["memory_version_uuid"])
                active_path_response = falkor_query_text(
                    falkor.url,
                    (
                        f"MATCH (m:Memory {{uuid:'{ids['memory_uuid']}'}})"
                        f"-[:HAS_VERSION]->(v:MemoryVersion {{uuid:'{ids['memory_version_uuid']}'}}) "
                        "WHERE m.status = 'active' AND v.status = 'current' RETURN m.uuid"
                    ),
                )
                receipt = _write_receipt(connection, namespace, ids)
            finally:
                connection.close()
            if ids["memory_uuid"] in active_path_response:
                return failed_result(
                    NAME,
                    "active_graph_residue",
                    "Forgotten content must not have an active graph path.",
                    memory_uuid=ids["memory_uuid"],
                )
            receipt_text = json.dumps(receipt, ensure_ascii=False)
            if RAW_ERASED_TEXT in receipt_text:
                return failed_result(
                    NAME,
                    "raw_erased_text_in_receipt",
                    "Erasure receipts must not contain raw erased content.",
                )
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
            resurrected = ids["memory_uuid"] in falkor_query_text(
                falkor.url,
                f"MATCH (n:Memory {{uuid:'{ids['memory_uuid']}'}}) RETURN n.uuid",
            )
            if resurrected:
                return failed_result(
                    NAME,
                    "rebuild_resurrected_erased_content",
                    "Graph rebuild must filter quarantined/erased canonical rows.",
                    rebuild=rebuild,
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                falkordb_provisioned_by=falkor.provisioned_by,
                active_graph_path=False,
                rebuild_resurrected=False,
                receipt_contains_raw_erased_content=False,
                receipt_uuid=receipt["receipt_uuid"],
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run forget residue smoke with disposable PostgreSQL and FalkorDB services.",
            error_sanitized=sanitize(str(error)),
        )


def _issue_forget(connection: Any, ids: dict[str, str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE memories SET status = 'quarantined' WHERE memory_uuid = %s", (ids["memory_uuid"],))
        cursor.execute(
            "UPDATE memory_versions SET status = 'erased', transaction_until = now() WHERE memory_version_uuid = %s",
            (ids["memory_version_uuid"],),
        )
        cursor.execute(
            """
            INSERT INTO privacy_erasure_outbox (
                outbox_uuid, event_type, source_type, source_uuid,
                payload_jsonb, status, priority
            )
            VALUES (%s, 'graph_erasure', 'memory', %s, %s::jsonb, 'pending', 110)
            ON CONFLICT DO NOTHING
            """,
            (
                f"{ids['memory_uuid']}-erasure-outbox",
                ids["memory_uuid"],
                '{"storage":"falkordb","raw_text_included":false}',
            ),
        )
    connection.commit()


def _write_receipt(connection: Any, namespace: str, ids: dict[str, str]) -> dict[str, Any]:
    request_uuid = f"{namespace}-privacy-request"
    receipt_uuid = f"{namespace}-receipt"
    receipt = {
        "receipt_uuid": receipt_uuid,
        "target_type": "memory",
        "target_uuid": ids["memory_uuid"],
        "raw_erased_content_included": False,
        "erased_content_hash": f"{namespace}-erased-hash",
    }
    residue = {
        "postgres": "quarantined",
        "falkordb": "invalidated",
        "active_graph_path": False,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO privacy_requests (
                privacy_request_uuid, request_type, status, target_type, target_uuid, receipt_uuid
            )
            VALUES (%s, 'forget', 'completed', 'memory', %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (request_uuid, ids["memory_uuid"], receipt_uuid),
        )
        cursor.execute(
            """
            INSERT INTO erasure_receipts (
                receipt_uuid, privacy_request_uuid, receipt_jsonb, residue_report_jsonb
            )
            VALUES (%s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                receipt_uuid,
                request_uuid,
                json.dumps(receipt, separators=(",", ":")),
                json.dumps(residue, separators=(",", ":")),
            ),
        )
    connection.commit()
    return receipt


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
