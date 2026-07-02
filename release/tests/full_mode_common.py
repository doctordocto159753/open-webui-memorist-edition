from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

FULL_CERT_BLOCKER = True


@dataclass(frozen=True)
class ExternalService:
    url: str
    provisioned_by: str
    container_name: str | None = None


class SkipSmoke(Exception):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(str(result.get("skip_reason", "skipped")))
        self.result = result


def now_z() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_namespace(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def passed_result(name: str, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": "passed",
        "passed": True,
        "skipped": False,
        "blocks_full_certification": False,
        "created_at": now_z(),
        **details,
    }


def failed_result(
    name: str,
    failing_step: str,
    remediation_hint: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed",
        "passed": False,
        "skipped": False,
        "blocks_full_certification": True,
        "failing_step": failing_step,
        "remediation_hint": remediation_hint,
        "created_at": now_z(),
        **details,
    }


def skipped_result(name: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": "skipped",
        "passed": False,
        "skipped": True,
        "skip_reason": reason,
        "blocks_full_certification": True,
        "created_at": now_z(),
        **details,
    }


def docker_daemon_status() -> tuple[bool, str | None]:
    try:
        completed = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except FileNotFoundError:
        return False, "Docker CLI is not installed."
    except Exception as error:
        return False, sanitize(str(error))
    if completed.returncode != 0:
        return False, sanitize(completed.stdout)
    return True, None


@contextmanager
def postgres_service(test_name: str) -> Iterator[ExternalService]:
    dsn = os.environ.get("MEMORIST_TEST_POSTGRES_DSN") or os.environ.get("MEMORIST_POSTGRES_DSN")
    if dsn:
        yield ExternalService(url=dsn, provisioned_by="env")
        return

    docker_ok, docker_reason = docker_daemon_status()
    if not docker_ok:
        raise SkipSmoke(
            skipped_result(
                test_name,
                "MEMORIST_TEST_POSTGRES_DSN is not set and Docker is unavailable.",
                docker_reason=docker_reason,
            )
        )
    if os.environ.get("MEMORIST_FULL_SMOKE_AUTO_DOCKER", "true").lower() == "false":
        raise SkipSmoke(
            skipped_result(
                test_name,
                "Docker is available, but MEMORIST_FULL_SMOKE_AUTO_DOCKER=false.",
            )
        )

    container_name = f"memorist-full-pg-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    dsn = f"postgresql://memorist:memorist@127.0.0.1:{port}/memorist"
    _docker_run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-e",
            "POSTGRES_DB=memorist",
            "-e",
            "POSTGRES_USER=memorist",
            "-e",
            "POSTGRES_PASSWORD=memorist",
            "-p",
            f"{port}:5432",
            "postgres:16-alpine",
        ],
        test_name,
        "start_postgres_container",
    )
    try:
        wait_for_postgres(dsn)
        yield ExternalService(url=dsn, provisioned_by="docker", container_name=container_name)
    finally:
        _docker_stop(container_name)


@contextmanager
def falkordb_service(test_name: str) -> Iterator[ExternalService]:
    url = os.environ.get("MEMORIST_TEST_FALKORDB_URL") or os.environ.get("MEMORIST_FALKORDB_URL")
    if url:
        yield ExternalService(url=url, provisioned_by="env")
        return

    docker_ok, docker_reason = docker_daemon_status()
    if not docker_ok:
        raise SkipSmoke(
            skipped_result(
                test_name,
                "MEMORIST_TEST_FALKORDB_URL is not set and Docker is unavailable.",
                docker_reason=docker_reason,
            )
        )
    if os.environ.get("MEMORIST_FULL_SMOKE_AUTO_DOCKER", "true").lower() == "false":
        raise SkipSmoke(
            skipped_result(
                test_name,
                "Docker is available, but MEMORIST_FULL_SMOKE_AUTO_DOCKER=false.",
            )
        )

    container_name = f"memorist-full-falkor-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    url = f"redis://127.0.0.1:{port}"
    _docker_run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-p",
            f"{port}:6379",
            "falkordb/falkordb:latest",
        ],
        test_name,
        "start_falkordb_container",
    )
    try:
        wait_for_falkordb(url)
        yield ExternalService(url=url, provisioned_by="docker", container_name=container_name)
    finally:
        _docker_stop(container_name)


def connect_postgres(dsn: str) -> Any:
    try:
        import psycopg
    except Exception as error:
        raise RuntimeError("psycopg is required; run the smoke under `uv run python`.") from error
    return psycopg.connect(dsn)


def apply_postgres_schema(connection: Any) -> int | None:
    from memcore.storage.postgres.migrations import (
        apply_postgres_migrations,
        postgres_schema_version,
    )

    apply_postgres_migrations(connection)
    return postgres_schema_version(connection)


def wait_for_postgres(dsn: str, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            connection = connect_postgres(dsn)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                return
            finally:
                connection.close()
        except Exception as error:
            last_error = sanitize(str(error))
            time.sleep(1)
    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


def wait_for_falkordb(url: str, timeout_seconds: int = 60) -> None:
    from memcore.graph.falkordb import FalkorDBClient

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        health = FalkorDBClient(url).health()
        if health.ok:
            return
        last_error = health.error_sanitized or health.status
        time.sleep(1)
    raise RuntimeError(f"FalkorDB did not become ready: {sanitize(last_error)}")


def seed_canonical_dataset(connection: Any, namespace: str) -> dict[str, str]:
    ids = {
        "workspace_uuid": f"{namespace}-workspace",
        "project_uuid": f"{namespace}-project",
        "session_uuid": f"{namespace}-session",
        "message_uuid": f"{namespace}-message",
        "message_version_uuid": f"{namespace}-message-version",
        "processing_run_uuid": f"{namespace}-processing",
        "text_unit_uuid": f"{namespace}-unit",
        "analysis_run_uuid": f"{namespace}-analysis",
        "annotation_uuid": f"{namespace}-annotation",
        "route_uuid": f"{namespace}-route",
        "candidate_uuid": f"{namespace}-candidate",
        "evidence_uuid": f"{namespace}-evidence",
        "memory_uuid": f"{namespace}-memory",
        "memory_version_uuid": f"{namespace}-memory-version",
        "evidence_link_uuid": f"{namespace}-evidence-link",
        "model_profile_uuid": f"{namespace}-model",
        "usage_event_uuid": f"{namespace}-usage",
        "outbox_uuid": f"{namespace}-graph-outbox",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workspaces (workspace_uuid, name)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (ids["workspace_uuid"], "Full Mode Smoke Workspace"),
        )
        cursor.execute(
            """
            INSERT INTO projects (project_uuid, workspace_uuid, name)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (ids["project_uuid"], ids["workspace_uuid"], "Full Mode Smoke Project"),
        )
        cursor.execute(
            """
            INSERT INTO sessions (session_uuid, workspace_uuid, project_uuid, title)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                ids["session_uuid"],
                ids["workspace_uuid"],
                ids["project_uuid"],
                "Full Mode Smoke Session",
            ),
        )
        cursor.execute(
            """
            INSERT INTO model_profiles (
                model_profile_uuid, provider, model_name, role,
                is_local, supports_structured_output, supports_json_mode
            )
            VALUES (%s, 'deterministic', 'memorist-smoke', 'memory_extraction', true, true, true)
            ON CONFLICT DO NOTHING
            """,
            (ids["model_profile_uuid"],),
        )
        cursor.execute(
            """
            INSERT INTO messages (
                message_uuid, session_uuid, turn_index, role, creator_type,
                model_profile_uuid, raw_text, raw_payload_jsonb, snapshot_jsonb,
                processing_status
            )
            VALUES (%s, %s, 1, 'user', 'user', %s, %s, %s::jsonb, %s::jsonb, 'processed')
            ON CONFLICT DO NOTHING
            """,
            (
                ids["message_uuid"],
                ids["session_uuid"],
                ids["model_profile_uuid"],
                "Please keep the full mode certification workflow visible.",
                json.dumps({"source": "full-smoke"}, separators=(",", ":")),
                json.dumps({"text": "full mode certification workflow"}, separators=(",", ":")),
            ),
        )
        cursor.execute(
            """
            INSERT INTO message_versions (
                message_version_uuid, message_uuid, version_number,
                raw_text, raw_payload_jsonb, snapshot_jsonb, change_reason, created_by
            )
            VALUES (%s, %s, 1, %s, %s::jsonb, %s::jsonb, 'initial', 'full-smoke')
            ON CONFLICT DO NOTHING
            """,
            (
                ids["message_version_uuid"],
                ids["message_uuid"],
                "Please keep the full mode certification workflow visible.",
                json.dumps({"source": "full-smoke"}, separators=(",", ":")),
                json.dumps({"text": "full mode certification workflow"}, separators=(",", ":")),
            ),
        )
        cursor.execute(
            """
            INSERT INTO memory_processing_runs (
                processing_run_uuid, session_uuid, message_uuid, pipeline_version,
                prompt_bundle_version, input_content_hash, status, started_at, finished_at
            )
            VALUES (%s, %s, %s, 'jakobson-v1', 'prompt-pack-v2', %s, 'succeeded', now(), now())
            ON CONFLICT DO NOTHING
            """,
            (
                ids["processing_run_uuid"],
                ids["session_uuid"],
                ids["message_uuid"],
                f"{namespace}-input-hash",
            ),
        )
        cursor.execute(
            """
            INSERT INTO text_units (
                text_unit_uuid, unit_uuid, message_uuid, session_uuid, speaker_role,
                unit_type, unit_index, text, start_char, end_char, content_hash
            )
            VALUES (%s, %s, %s, %s, 'user', 'sentence', 0, %s, 0, 58, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                ids["text_unit_uuid"],
                ids["text_unit_uuid"],
                ids["message_uuid"],
                ids["session_uuid"],
                "Please keep the full mode certification workflow visible.",
                f"{namespace}-unit-hash",
            ),
        )
        cursor.execute(
            """
            INSERT INTO jakobson_analysis_runs (
                analysis_run_uuid, workspace_uuid, project_uuid, session_uuid, message_uuid,
                prompt_id, prompt_version, model_profile_uuid, provider_type, model_name,
                input_hash, output_hash, status, warnings_jsonb, raw_output_jsonb
            )
            VALUES (
                %s, %s, %s, %s, %s,
                'memorist.jakobson_sentence_analysis', '2.0', %s,
                'deterministic', 'memorist-smoke', %s, %s, 'succeeded', '[]'::jsonb, %s::jsonb
            )
            ON CONFLICT DO NOTHING
            """,
            (
                ids["analysis_run_uuid"],
                ids["workspace_uuid"],
                ids["project_uuid"],
                ids["session_uuid"],
                ids["message_uuid"],
                ids["model_profile_uuid"],
                f"{namespace}-analysis-in",
                f"{namespace}-analysis-out",
                json.dumps({"dominant_function": "conative"}, separators=(",", ":")),
            ),
        )
        cursor.execute(
            """
            INSERT INTO jakobson_sentence_annotations (
                annotation_uuid, analysis_run_uuid, message_uuid, unit_uuid,
                sentence_index, sentence_text, sentence_hash,
                sender_value, sender_confidence,
                receiver_value, receiver_confidence,
                message_value, message_confidence,
                context_value, context_confidence,
                code_value, code_confidence,
                contact_channel_value, contact_channel_confidence,
                dominant_function, secondary_functions_jsonb,
                function_reason, raw_sentence_output_jsonb
            )
            VALUES (
                %s, %s, %s, %s, 0, %s, %s,
                'user', 'high', 'assistant', 'high',
                'preserve full mode certification workflow', 'high',
                'Full Mode reliability sprint', 'high',
                'English', 'high', 'chat', 'medium',
                'conative', '[]'::jsonb, 'explicit user instruction', %s::jsonb
            )
            ON CONFLICT DO NOTHING
            """,
            (
                ids["annotation_uuid"],
                ids["analysis_run_uuid"],
                ids["message_uuid"],
                ids["text_unit_uuid"],
                "Please keep the full mode certification workflow visible.",
                f"{namespace}-sentence-hash",
                json.dumps({"dominant_function": "conative"}, separators=(",", ":")),
            ),
        )
        cursor.execute(
            """
            INSERT INTO memory_signal_routes (
                route_uuid, annotation_uuid, message_uuid, unit_uuid, dominant_function,
                secondary_functions_jsonb, route_type, extractor_id, priority,
                confidence, reason, status
            )
            VALUES (
                %s, %s, %s, %s, 'conative', '[]'::jsonb,
                'instruction', 'deterministic.conative', 90, 'high',
                'explicit instruction', 'ready'
            )
            ON CONFLICT DO NOTHING
            """,
            (
                ids["route_uuid"],
                ids["annotation_uuid"],
                ids["message_uuid"],
                ids["text_unit_uuid"],
            ),
        )
        cursor.execute(
            """
            INSERT INTO memory_candidates (
                candidate_uuid, processing_run_uuid, text_unit_uuid, candidate_type,
                subject_key, predicate, object_jsonb, normalized_text, source_authority,
                explicitness, confidence, importance, sensitivity, status,
                extraction_metadata_jsonb
            )
            VALUES (
                %s, %s, %s, 'instruction', 'project.workflow',
                'should_preserve', %s::jsonb, %s, 'user',
                'explicit', 0.95, 0.9, 'normal', 'accepted', %s::jsonb
            )
            ON CONFLICT DO NOTHING
            """,
            (
                ids["candidate_uuid"],
                ids["processing_run_uuid"],
                ids["text_unit_uuid"],
                json.dumps({"value": "full mode certification workflow"}, separators=(",", ":")),
                "Keep the full mode certification workflow visible.",
                json.dumps({"route_uuid": ids["route_uuid"]}, separators=(",", ":")),
            ),
        )
        cursor.execute(
            """
            INSERT INTO candidate_evidence (
                evidence_uuid, candidate_uuid, message_uuid, text_unit_uuid,
                annotation_uuid, route_uuid, evidence_text, start_char, end_char
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 58)
            ON CONFLICT DO NOTHING
            """,
            (
                ids["evidence_uuid"],
                ids["candidate_uuid"],
                ids["message_uuid"],
                ids["text_unit_uuid"],
                ids["annotation_uuid"],
                ids["route_uuid"],
                "Please keep the full mode certification workflow visible.",
            ),
        )
        cursor.execute(
            """
            INSERT INTO memories (
                memory_uuid, scope_type, scope_uuid, canonical_key,
                current_version_uuid, status
            )
            VALUES (%s, 'project', %s, %s, %s, 'active')
            ON CONFLICT DO NOTHING
            """,
            (
                ids["memory_uuid"],
                ids["project_uuid"],
                f"{namespace}:project.workflow",
                ids["memory_version_uuid"],
            ),
        )
        cursor.execute(
            """
            INSERT INTO memory_versions (
                memory_version_uuid, memory_uuid, version_number, operation, value,
                normalized_text, confidence, importance, source_snapshot_hash,
                valid_from, status
            )
            VALUES (%s, %s, 1, 'create', %s, %s, 0.95, 0.9, %s, now(), 'current')
            ON CONFLICT DO NOTHING
            """,
            (
                ids["memory_version_uuid"],
                ids["memory_uuid"],
                "Keep the full mode certification workflow visible.",
                "keep full mode certification workflow visible",
                f"{namespace}-source-hash",
            ),
        )
        cursor.execute(
            """
            UPDATE memories
            SET current_version_uuid = %s
            WHERE memory_uuid = %s
            """,
            (ids["memory_version_uuid"], ids["memory_uuid"]),
        )
        cursor.execute(
            """
            INSERT INTO memory_evidence_links (
                link_uuid, memory_uuid, memory_version_uuid, candidate_uuid, evidence_uuid
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                ids["evidence_link_uuid"],
                ids["memory_uuid"],
                ids["memory_version_uuid"],
                ids["candidate_uuid"],
                ids["evidence_uuid"],
            ),
        )
        cursor.execute(
            """
            INSERT INTO model_usage_events (
                usage_event_uuid, model_profile_uuid, role, event_type,
                input_tokens, output_tokens, cost_jsonb,
                stage, provider_type, model_name, workspace_uuid,
                project_uuid, session_uuid, message_uuid, unit_uuid,
                annotation_uuid, status
            )
            VALUES (
                %s, %s, 'memory_extraction', 'completion',
                24, 18, %s::jsonb, 'jakobson_analysis',
                'deterministic', 'memorist-smoke', %s, %s, %s, %s, %s, %s, 'ok'
            )
            ON CONFLICT DO NOTHING
            """,
            (
                ids["usage_event_uuid"],
                ids["model_profile_uuid"],
                json.dumps({"currency": "USD", "amount": 0}, separators=(",", ":")),
                ids["workspace_uuid"],
                ids["project_uuid"],
                ids["session_uuid"],
                ids["message_uuid"],
                ids["text_unit_uuid"],
                ids["annotation_uuid"],
            ),
        )
        cursor.execute(
            """
            INSERT INTO graph_projection_outbox (
                outbox_uuid, event_type, source_type, source_uuid,
                payload_jsonb, status, priority
            )
            VALUES (%s, 'memory_version_projected', 'memory_version', %s, %s::jsonb, 'pending', 70)
            ON CONFLICT DO NOTHING
            """,
            (
                ids["outbox_uuid"],
                ids["memory_version_uuid"],
                json.dumps({"memory_uuid": ids["memory_uuid"]}, separators=(",", ":")),
            ),
        )
    connection.commit()
    return ids


def fetch_one_value(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return None if row is None else row[0]


def fetch_count(connection: Any, table_name: str, column_name: str, value: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE {column_name} = %s",
            (value,),
        )
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def projection_record(connection: Any, ids: dict[str, str]) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                w.workspace_uuid,
                p.project_uuid,
                s.session_uuid,
                msg.message_uuid,
                tu.text_unit_uuid AS unit_uuid,
                jsa.annotation_uuid,
                msr.route_uuid,
                mc.candidate_uuid,
                mem.memory_uuid,
                mv.memory_version_uuid,
                jsa.dominant_function,
                jsa.receiver_value,
                jsa.context_value,
                jsa.code_value,
                mem.status AS memory_status,
                mv.status AS version_status,
                mv.value AS memory_value
            FROM memories mem
            JOIN memory_versions mv ON mv.memory_version_uuid = mem.current_version_uuid
            JOIN memory_evidence_links mel ON mel.memory_uuid = mem.memory_uuid
            JOIN memory_candidates mc ON mc.candidate_uuid = mel.candidate_uuid
            JOIN candidate_evidence ce ON ce.evidence_uuid = mel.evidence_uuid
            JOIN messages msg ON msg.message_uuid = ce.message_uuid
            JOIN sessions s ON s.session_uuid = msg.session_uuid
            JOIN projects p ON p.project_uuid = s.project_uuid
            JOIN workspaces w ON w.workspace_uuid = s.workspace_uuid
            JOIN text_units tu ON tu.text_unit_uuid = ce.text_unit_uuid
            JOIN jakobson_sentence_annotations jsa ON jsa.annotation_uuid = ce.annotation_uuid
            JOIN memory_signal_routes msr ON msr.route_uuid = ce.route_uuid
            WHERE mem.memory_uuid = %s
            """,
            (ids["memory_uuid"],),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("seeded projection record was not found")
        columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row, strict=False))


def falkor_query_text(url: str, query: str) -> str:
    from memcore.graph.falkordb import FalkorDBClient

    response = FalkorDBClient(url).graph_query(query)
    return response.decode("utf-8", errors="replace")


def assert_falkor_match(url: str, query: str, expected: str) -> None:
    response = falkor_query_text(url, query)
    if expected not in response:
        raise RuntimeError(f"FalkorDB query did not return {expected}: {sanitize(response)}")


def sanitize(value: str, limit: int = 240) -> str:
    redacted = value
    for marker in ("password=", "POSTGRES_PASSWORD=", "token=", "secret="):
        if marker in redacted:
            redacted = redacted.replace(marker, f"{marker}[redacted]")
    return redacted[:limit]


def print_result(result: dict[str, Any]) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") or result.get("skipped") else 1


def _docker_run(argv: list[str], test_name: str, failing_step: str) -> None:
    completed = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            json.dumps(
                failed_result(
                    test_name,
                    failing_step,
                    "Check Docker daemon, image availability, and local port availability.",
                    docker_output=sanitize(completed.stdout),
                )
            )
        )


def _docker_stop(container_name: str) -> None:
    subprocess.run(
        ["docker", "stop", container_name],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
