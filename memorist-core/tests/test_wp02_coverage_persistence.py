from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from memcore.governance.privacy import PrivacyService
from memcore.heritage.package import export_heritage, restore_heritage
from memcore.memory_worker.postgres.semantic_coverage import (
    PostgresSemanticCoverageRepository,
)
from memcore.memory_worker.prompts.contracts import (
    SEMANTIC_CANDIDATE_V1_CONTRACT,
    SemanticAnalysisV1Output,
)
from memcore.memory_worker.semantic.candidate_mapping import (
    ROUTE_CANDIDATE_MAPPING_VERSION,
)
from memcore.memory_worker.semantic.coverage import (
    CandidateProposal,
    CoveragePlan,
    CoveragePlannerInput,
    PersistedUnitAuthority,
    plan_candidate_coverage,
)
from memcore.memory_worker.semantic.provenance_policy import PROVENANCE_POLICY_VERSION
from memcore.memory_worker.semantic_coverage_persistence import (
    CandidateAuthorityBinding,
    CoveragePersistenceBindings,
    SemanticCoverageIdentityConflict,
    candidate_payload_hash,
    deterministic_evidence,
)
from memcore.migrate.sqlite_to_postgres import commit as migrate_sqlite_to_postgres
from memcore.models import (
    CandidateEvidence,
    CandidateStatus,
    CandidateType,
    EvidenceRole,
    Explicitness,
    MemoryCandidate,
    Polarity,
    SensitivityClass,
    SourceAuthority,
    SupportType,
    utc_now,
)
from memcore.privacy.residue_check import run_forget_residue_check
from memcore.repositories.semantic_coverage import (
    CreateAndLinkSemanticCandidateCommand,
    PersistCoveragePlanCommand,
    ReserveSemanticCandidateCommand,
    SQLiteSemanticCoverageRepository,
)
from memcore.repositories.sqlite import SQLiteRepository
from memcore.storage.migrations import (
    apply_migrations,
    default_migrations_dir,
    migration_files,
)
from memcore.storage.postgres.migrations import (
    apply_postgres_migrations,
    default_postgres_migrations_dir,
    postgres_migration_files,
    postgres_migration_version,
)
from memcore.storage.postgres.parity import build_parity_report
from memcore.storage.sqlite import connect
from memcore.storage.write_actor import SQLiteWriteActor
from memcore.textsemantics.result import build_envelope
from memcore.validators.ijson import dump_ijson, load_ijson

RAW = "Keep backups enabled."


def _seed_authority(connection: sqlite3.Connection) -> None:
    now = utc_now()
    raw_hash = hashlib.sha256(RAW.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO sessions (
          session_uuid, source_app, created_at, status, privacy_scope, schema_version
        ) VALUES ('00000000-0000-4000-8000-000000000001', 'test', ?, 'active', 'local', 1)
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO messages (
          message_uuid, session_uuid, turn_index, role, creator_type, raw_text, content_hash,
          created_at, processing_status, visibility, is_deleted, redaction_status,
          schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000001',
          98201, 'user', 'user', ?, ?, ?, 'pending',
          'visible', 0, 'none', 1
        )
        """,
        (RAW, raw_hash, now),
    )
    connection.execute(
        """
        INSERT INTO memory_processing_runs (
          processing_run_uuid, session_uuid, message_uuid, pipeline_version,
          input_content_hash, status, created_at, schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000003',
          '00000000-0000-4000-8000-000000000001',
          '00000000-0000-4000-8000-000000000002',
          'test', ?, 'succeeded', ?, 1
        )
        """,
        (raw_hash, now),
    )
    connection.execute(
        """
        INSERT INTO text_units (
          text_unit_uuid, unit_uuid, message_uuid, session_uuid, unit_index,
          unit_type, text, start_char, end_char, char_start, char_end,
          speaker_role, content_hash, created_at, schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000004',
          '00000000-0000-4000-8000-000000000004',
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000001',
          0, 'sentence', ?,
          0, ?, 0, ?, 'user', ?, ?, 1
        )
        """,
        (RAW, len(RAW), len(RAW), raw_hash, now),
    )
    connection.execute(
        """
        INSERT INTO memory_gate_decisions (
          gate_decision_uuid, text_unit_uuid, processing_run_uuid, decision,
          reason_codes_ijson, salience_score, persistence_score,
          actionability_score, sensitivity_score, novelty_score,
          requires_high_confidence_pass, created_at, schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000005',
          '00000000-0000-4000-8000-000000000004',
          '00000000-0000-4000-8000-000000000003',
          'analyze', '[]',
          1, 1, 1, 0, 1, 0, ?, 1
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO jakobson_analysis_runs (
          analysis_run_uuid, session_uuid, message_uuid, prompt_id, prompt_version,
          provider_type, input_hash, output_hash, status, warnings_ijson,
          created_at, schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000006',
          '00000000-0000-4000-8000-000000000001',
          '00000000-0000-4000-8000-000000000002',
          'memorist.jakobson_sentence_analysis', '3.0', 'deterministic',
          ?, ?, 'succeeded', '[]', ?, 1
        )
        """,
        (raw_hash, raw_hash, now),
    )
    connection.execute(
        """
        INSERT INTO jakobson_sentence_annotations (
          annotation_uuid, analysis_run_uuid, message_uuid, unit_uuid,
          sentence_index, sentence_text, sentence_hash,
          sender_confidence, receiver_confidence, message_confidence,
          context_confidence, code_confidence, contact_channel_confidence,
          dominant_function, secondary_functions_ijson,
          raw_sentence_output_ijson, created_at, schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000007',
          '00000000-0000-4000-8000-000000000006',
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000004',
          1, ?, ?,
          'high', 'high', 'high', 'high', 'high', 'high', 'referential',
          '[]', '{}', ?, 1
        )
        """,
        (RAW, raw_hash, now),
    )
    connection.execute(
        """
        INSERT INTO memory_signal_routes (
          route_uuid, annotation_uuid, message_uuid, unit_uuid,
          dominant_function, secondary_functions_ijson, route_type,
          extractor_id, priority, confidence, reason, status, created_at,
          schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000008',
          '00000000-0000-4000-8000-000000000007',
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000004',
          'referential',
          '[]', 'project_context', 'test', 1, 'high', 'test', 'ready', ?, 1
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO prompt_execution_runs (
          prompt_execution_uuid, prompt_id, prompt_version, stage, model_role,
          provider_type, model_name, session_uuid, message_uuid, input_hash,
          output_hash, input_ref, raw_output_ijson, validated_output_ijson,
          status, warnings_ijson, error_sanitized, input_tokens, output_tokens,
          created_at, schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000009',
          'memorist.semantic_candidate_analysis', '1.0',
          'semantic_candidate_analysis', 'memory_extraction', 'deterministic',
          'test',
          '00000000-0000-4000-8000-000000000001',
          '00000000-0000-4000-8000-000000000002',
          ?, ?, ?, ?, ?, 'ok', ?, ?, 0, 1, ?, 1
        )
        """,
        (
            raw_hash,
            raw_hash,
            RAW,
            dump_ijson({"semantic_units": [{"evidence": RAW}]}),
            dump_ijson({"semantic_units": [{"evidence": RAW}]}),
            dump_ijson([RAW]),
            RAW,
            now,
        ),
    )
    connection.commit()


def _plan() -> tuple[CoveragePlan, CandidateProposal, CoveragePersistenceBindings]:
    analysis = SemanticAnalysisV1Output.model_validate(
        {
            "schema_version": "1.0",
            "prompt_id": "memorist.semantic_candidate_analysis",
            "prompt_version": "1.0",
            "status": "ok",
            "warnings": [],
            "semantic_units": [
                {
                    "id": "model-unit-a",
                    "raw_start": 0,
                    "raw_end": len(RAW),
                    "evidence": RAW,
                    "proposition": "Backups must remain enabled.",
                    "unit_type": "instruction",
                    "durability": "durable",
                    "polarity": "affirmed",
                    "epistemic_status": "asserted",
                }
            ],
            "references": [],
            "relations": [],
        }
    )
    value = CoveragePlannerInput(
        message_uuid="00000000-0000-4000-8000-000000000002",
        message_version_uuid=None,
        message_role="user",
        processing_run_uuid="00000000-0000-4000-8000-000000000003",
        current_raw_text=RAW,
        text_envelope=build_envelope(RAW).as_dict(),
        semantic_analysis=analysis,
        accepted_unit_ids=("model-unit-a",),
        accepted_reference_indexes=(),
        accepted_relation_indexes=(),
        authorities=(
            PersistedUnitAuthority(
                text_unit_uuid="00000000-0000-4000-8000-000000000004",
                raw_start=0,
                raw_end=len(RAW),
                annotation_uuid="00000000-0000-4000-8000-000000000007",
                gate_decision_uuid="00000000-0000-4000-8000-000000000005",
                gate_decision="analyze",
                route_uuid="00000000-0000-4000-8000-000000000008",
                route_type="project_context",
                route_status="ready",
                privacy_ceiling="normal",
                privacy_storage_allowed=True,
            ),
        ),
        semantic_prompt_execution_uuid="00000000-0000-4000-8000-000000000009",
        semantic_contract_hash=SEMANTIC_CANDIDATE_V1_CONTRACT.contract_hash,
        bounded_context_items=(),
        imported_record=False,
        route_mapping_version=ROUTE_CANDIDATE_MAPPING_VERSION,
        provenance_policy_version=PROVENANCE_POLICY_VERSION,
        privacy_policy_version="memorist.privacy.policy.v1",
    )
    plan, proposals = plan_candidate_coverage(value)
    proposal = proposals[0]
    bindings = CoveragePersistenceBindings(
        message_version_uuid=None,
        text_envelope_contract_version="memorist.text.envelope.v3",
        semantic_unit_fingerprints={proposal.semantic_unit_id: proposal.semantic_unit_fingerprint},
        annotation_uuids={
            item.coverage_item_id: "00000000-0000-4000-8000-000000000007" for item in plan.items
        },
    )
    return plan, proposal, bindings


def _candidate(
    proposal: CandidateProposal,
) -> tuple[MemoryCandidate, CandidateEvidence]:
    value = proposal
    candidate = MemoryCandidate(
        candidate_uuid=value.proposal_id,
        processing_run_uuid="00000000-0000-4000-8000-000000000003",
        text_unit_uuid=value.text_unit_uuid,
        prompt_execution_uuid=value.prompt_execution_uuid,
        candidate_type=CandidateType(value.candidate_type),
        subject_key=value.subject_key,
        predicate=value.predicate,
        object_ijson=dump_ijson(value.object_payload),
        normalized_text=value.normalized_text,
        source_authority=SourceAuthority(value.source_authority),
        explicitness=Explicitness(value.explicitness),
        confidence=0.70,
        polarity=Polarity(value.polarity),
        importance=0.70,
        status=CandidateStatus(value.status),
        sensitivity_class=SensitivityClass(value.privacy_ceiling),
        extraction_metadata_ijson=dump_ijson(
            {"semantic_unit_fingerprint": value.semantic_unit_fingerprint}
        ),
        rejection_reason_codes_ijson=dump_ijson(list(value.reason_codes)),
    )
    evidence = CandidateEvidence(
        candidate_uuid=value.proposal_id,
        message_uuid=value.message_uuid,
        text_unit_uuid=value.text_unit_uuid,
        annotation_uuid=value.annotation_uuid,
        route_uuid=value.route_uuid,
        evidence_text=value.evidence,
        start_char=value.raw_start,
        end_char=value.raw_end,
        evidence_role=EvidenceRole.PRIMARY,
        support_type=SupportType.SUPPORTING,
    )
    return candidate, evidence


def _authority_binding() -> CandidateAuthorityBinding:
    return CandidateAuthorityBinding(
        processing_run_uuid="00000000-0000-4000-8000-000000000003",
        text_unit_uuid="00000000-0000-4000-8000-000000000004",
        gate_decision_uuid="00000000-0000-4000-8000-000000000005",
        gate_decision="analyze",
        annotation_uuid="00000000-0000-4000-8000-000000000007",
        route_uuid="00000000-0000-4000-8000-000000000008",
        route_type="project_context",
        route_status="ready",
    )


def test_sqlite_fresh_upgrade_repeated_and_content_free_schema(tmp_path: Path) -> None:
    migrations = default_migrations_dir()
    old = tmp_path / "old-migrations"
    old.mkdir()
    for source in migration_files(migrations):
        if source.name < "0037_semantic_coverage_audit.sql":
            shutil.copy2(source, old / source.name)
    connection = connect(tmp_path / "upgrade.sqlite")
    apply_migrations(connection, old)
    connection.execute(
        """
        INSERT INTO sessions (
          session_uuid, source_app, created_at, status, privacy_scope, schema_version
        ) VALUES ('populated', 'test', ?, 'active', 'local', 1)
        """,
        (utc_now(),),
    )
    connection.commit()
    apply_migrations(connection, migrations)
    apply_migrations(connection, migrations)
    assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    columns = {
        row["name"]
        for table in (
            "semantic_coverage_runs",
            "semantic_coverage_items",
            "semantic_candidate_links",
        )
        for row in connection.execute(f"PRAGMA table_info({table})")
    }
    assert (
        not {
            "evidence",
            "evidence_text",
            "proposition",
            "prior_context_text",
            "provider_secret",
        }
        & columns
    )
    latest = connection.execute(
        "SELECT migration_id FROM schema_migrations ORDER BY migration_id DESC LIMIT 1"
    ).fetchone()[0]
    assert latest == "0037_semantic_coverage_audit.sql"


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_postgres_populated_0023_to_0024_upgrade_is_additive_and_repeatable(
    tmp_path: Path,
) -> None:
    psycopg = importlib.import_module("psycopg")
    sql = importlib.import_module("psycopg.sql")
    schema_name = f"wp02_upgrade_{uuid.uuid4().hex}"
    old = tmp_path / "postgres-0023"
    old.mkdir()
    for source in postgres_migration_files(default_postgres_migrations_dir()):
        if postgres_migration_version(source) < 24:
            shutil.copy2(source, old / source.name)

    connection = psycopg.connect(os.environ["MEMORIST_POSTGRES_DSN"])
    try:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        connection.commit()
        apply_postgres_migrations(connection, old)
        connection.execute(
            """
            INSERT INTO workspaces (workspace_uuid, name)
            VALUES ('wp02-populated-workspace', 'WP02 populated upgrade')
            """
        )
        connection.commit()

        assert (
            connection.execute("SELECT to_regclass('semantic_coverage_runs')").fetchone()[0] is None
        )
        prior_evidence_columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'candidate_evidence'
                """
            ).fetchall()
        }
        assert "evidence_role" not in prior_evidence_columns
        assert "support_type" not in prior_evidence_columns

        apply_postgres_migrations(connection, default_postgres_migrations_dir())
        apply_postgres_migrations(connection, default_postgres_migrations_dir())

        assert (
            connection.execute(
                """
            SELECT name
            FROM workspaces
            WHERE workspace_uuid = 'wp02-populated-workspace'
            """
            ).fetchone()[0]
            == "WP02 populated upgrade"
        )
        assert {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name IN (
                    'semantic_coverage_runs',
                    'semantic_coverage_items',
                    'semantic_candidate_links'
                  )
                """
            ).fetchall()
        } == {
            "semantic_coverage_runs",
            "semantic_coverage_items",
            "semantic_candidate_links",
        }
        evidence_defaults = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT column_name, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'candidate_evidence'
                  AND column_name IN ('evidence_role', 'support_type')
                """
            ).fetchall()
        }
        assert "'primary'::text" in str(evidence_defaults["evidence_role"])
        assert "'supporting'::text" in str(evidence_defaults["support_type"])
        assert (
            connection.execute(
                """
            SELECT migration_id
            FROM schema_migrations
            ORDER BY migration_id DESC
            LIMIT 1
            """
            ).fetchone()[0]
            == "0024_semantic_coverage_audit.sql"
        )
    finally:
        connection.rollback()
        connection.execute("SET search_path TO public")
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        connection.commit()
        connection.close()


def test_sqlite_actor_replay_link_and_identity_conflict(tmp_path: Path) -> None:
    path = tmp_path / "actor.sqlite"
    connection = connect(path)
    apply_migrations(connection)
    _seed_authority(connection)
    plan, proposal, bindings = _plan()
    connection.close()

    actor = SQLiteWriteActor(path)
    try:
        first = actor.submit_sync(PersistCoveragePlanCommand(plan, bindings))
        replay = actor.submit_sync(PersistCoveragePlanCommand(plan, bindings))
        candidate, evidence = _candidate(proposal)
        payload_hash = candidate_payload_hash(candidate, (evidence,))
        item_id = plan.items[0].coverage_item_id
        actor.submit_sync(
            ReserveSemanticCandidateCommand(proposal.proposal_id, item_id, payload_hash)
        )
        linked = actor.submit_sync(
            CreateAndLinkSemanticCandidateCommand(proposal, candidate, (evidence,))
        )
        linked_replay = actor.submit_sync(
            CreateAndLinkSemanticCandidateCommand(proposal, candidate, (evidence,))
        )
    finally:
        actor.stop_sync()

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert linked.idempotent_replay is False
    assert linked_replay.idempotent_replay is True
    check = connect(path)
    assert check.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 1
    assert check.execute("SELECT COUNT(*) FROM candidate_evidence").fetchone()[0] == 1
    link = check.execute("SELECT * FROM semantic_candidate_links").fetchone()
    assert link["candidate_uuid"] == link["proposal_uuid"] == proposal.proposal_id
    deterministic_evidence_uuid = check.execute(
        "SELECT evidence_uuid FROM candidate_evidence"
    ).fetchone()[0]
    assert deterministic_evidence_uuid != evidence.evidence_uuid

    repository = SQLiteSemanticCoverageRepository(check)
    with pytest.raises(SemanticCoverageIdentityConflict):
        repository.reserve_candidate(proposal.proposal_id, item_id, "0" * 64)


def test_sqlite_failed_final_transaction_leaves_reservation(tmp_path: Path) -> None:
    connection = connect(tmp_path / "rollback.sqlite")
    apply_migrations(connection)
    _seed_authority(connection)
    plan, proposal, bindings = _plan()
    repository = SQLiteSemanticCoverageRepository(connection)
    repository.persist_plan(plan, bindings)
    candidate, evidence = _candidate(proposal)
    repository.reserve_candidate(
        proposal.proposal_id,
        plan.items[0].coverage_item_id,
        candidate_payload_hash(candidate, (evidence,)),
    )
    bad_candidate = candidate.model_copy(update={"candidate_uuid": "random"})
    with pytest.raises(SemanticCoverageIdentityConflict):
        repository.create_and_link_candidate(proposal, bad_candidate, (evidence,))
    assert connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 0
    link = connection.execute("SELECT * FROM semantic_candidate_links").fetchone()
    assert link["state"] == "candidate_creation_attempted"
    assert link["candidate_uuid"] is None


def test_sqlite_final_transaction_rereads_gate_and_route_authority(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "authority-mutation.sqlite")
    apply_migrations(connection)
    _seed_authority(connection)
    plan, proposal, bindings = _plan()
    repository = SQLiteSemanticCoverageRepository(connection)
    repository.persist_plan(plan, bindings)
    candidate, evidence = _candidate(proposal)
    repository.reserve_candidate(
        proposal.proposal_id,
        plan.items[0].coverage_item_id,
        candidate_payload_hash(candidate, (evidence,)),
    )
    connection.execute(
        """
        UPDATE memory_gate_decisions
        SET decision = 'retain_raw_only'
        WHERE gate_decision_uuid = '00000000-0000-4000-8000-000000000005'
        """
    )
    connection.commit()

    with pytest.raises(
        SemanticCoverageIdentityConflict,
        match="authority changed",
    ):
        repository.create_and_link_candidate(
            proposal,
            candidate,
            (evidence,),
            _authority_binding(),
        )

    assert connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 0
    link = connection.execute("SELECT * FROM semantic_candidate_links").fetchone()
    assert link["state"] == "candidate_creation_attempted"


def test_sqlite_crash_c_reconciles_existing_candidate_without_duplicate(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "crash-c.sqlite")
    apply_migrations(connection)
    _seed_authority(connection)
    plan, proposal, bindings = _plan()
    repository = SQLiteSemanticCoverageRepository(connection)
    repository.persist_plan(plan, bindings)
    candidate, evidence = _candidate(proposal)
    repository.reserve_candidate(
        proposal.proposal_id,
        plan.items[0].coverage_item_id,
        candidate_payload_hash(candidate, (evidence,)),
    )
    canonical_evidence = deterministic_evidence(proposal.proposal_id, evidence)
    sqlite = SQLiteRepository(connection)
    sqlite.insert("memory_candidates", candidate.model_dump(mode="json"))
    sqlite.insert("candidate_evidence", canonical_evidence.model_dump(mode="json"))
    connection.commit()

    result = repository.create_and_link_candidate(
        proposal,
        candidate,
        (evidence,),
        _authority_binding(),
    )

    assert result["state"] == "created"
    assert connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM candidate_evidence").fetchone()[0] == 1
    link = connection.execute("SELECT * FROM semantic_candidate_links").fetchone()
    assert link["state"] == "candidate_linked"
    assert link["candidate_uuid"] == proposal.proposal_id


def test_heritage_forget_and_residue_treat_coverage_as_audit_only(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "source.sqlite")
    apply_migrations(connection)
    _seed_authority(connection)
    connection.execute(
        """
        INSERT INTO message_versions (
          message_version_uuid, message_uuid, version_number, raw_text,
          raw_payload_ijson, snapshot_ijson, change_reason, created_by,
          created_at, content_hash, schema_version
        ) VALUES (
          '00000000-0000-4000-8000-000000000010',
          '00000000-0000-4000-8000-000000000002',
          1, ?, ?, ?, 'initial', 'test', ?, ?, 1
        )
        """,
        (
            RAW,
            dump_ijson({"raw_text": RAW}),
            dump_ijson({"raw_text": RAW}),
            utc_now(),
            hashlib.sha256(RAW.encode()).hexdigest(),
        ),
    )
    connection.commit()
    plan, proposal, bindings = _plan()
    repository = SQLiteSemanticCoverageRepository(connection)
    repository.persist_plan(plan, bindings)
    candidate, evidence = _candidate(proposal)
    repository.reserve_candidate(
        proposal.proposal_id,
        plan.items[0].coverage_item_id,
        candidate_payload_hash(candidate, (evidence,)),
    )
    repository.create_and_link_candidate(proposal, candidate, (evidence,))

    package = tmp_path / "coverage.zip"
    export_heritage(connection, package)
    restored_path = tmp_path / "restored.sqlite"
    outcome = restore_heritage(package, restored_path, dry_run=False)
    assert outcome["status"] == "restored"
    restored = connect(restored_path)
    assert restored.execute("SELECT COUNT(*) FROM semantic_coverage_runs").fetchone()[0] == 1
    assert restored.execute("SELECT COUNT(*) FROM semantic_candidate_links").fetchone()[0] == 1

    preview = PrivacyService(connection).preview_request(
        "delete_message",
        "message",
        {"message_uuid": "00000000-0000-4000-8000-000000000002"},
        actor_type="user",
        target_uuid="00000000-0000-4000-8000-000000000002",
    )
    PrivacyService(connection).confirm_request(
        preview["privacy_request_uuid"], preview["confirmation_token"]
    )
    receipt = PrivacyService(connection).execute_request(preview["privacy_request_uuid"])
    retained = load_ijson(receipt["retained_record_counts_ijson"])
    erased = load_ijson(receipt["erased_record_counts_ijson"])
    assert retained["semantic_candidate_link_audit"] == 1
    assert retained["semantic_coverage_item_audit"] == 1
    assert retained["semantic_coverage_run_audit"] == 1
    assert erased["message_version"] == 1
    assert erased["prompt_execution_run"] == 1
    assert RAW not in str(receipt)
    version = connection.execute(
        """
        SELECT raw_text, raw_payload_ijson, snapshot_ijson, change_reason
        FROM message_versions
        WHERE message_version_uuid = '00000000-0000-4000-8000-000000000010'
        """
    ).fetchone()
    assert tuple(version) == (None, None, None, "privacy_redacted")
    prompt = connection.execute(
        """
        SELECT input_ref, raw_output_ijson, validated_output_ijson,
               warnings_ijson, error_sanitized
        FROM prompt_execution_runs
        WHERE prompt_execution_uuid = '00000000-0000-4000-8000-000000000009'
        """
    ).fetchone()
    assert prompt["input_ref"] is None
    assert prompt["error_sanitized"] is None
    assert RAW not in str(tuple(prompt))
    residue = run_forget_residue_check(
        connection,
        "00000000-0000-4000-8000-000000000002",
        RAW,
        check_uuid_references=False,
    )
    assert residue["status"] == "clean"
    assert not {
        "semantic_coverage_runs",
        "semantic_coverage_items",
        "semantic_candidate_links",
    } & {item["table"] for item in residue["residue"]}


def _seed_postgres(connection: Any) -> None:
    raw_hash = hashlib.sha256(RAW.encode()).hexdigest()
    now = utc_now()
    connection.execute(
        """
        INSERT INTO sessions (session_uuid)
        VALUES ('00000000-0000-4000-8000-000000000001')
        ON CONFLICT DO NOTHING
        """
    )
    connection.execute(
        """
        INSERT INTO messages (
          message_uuid, session_uuid, turn_index, role, creator_type, raw_text
        ) VALUES (
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000001',
          98201, 'user', 'user', %s
        ) ON CONFLICT DO NOTHING
        """,
        (RAW,),
    )
    connection.execute(
        """
        INSERT INTO memory_processing_runs (
          processing_run_uuid, session_uuid, message_uuid, pipeline_version,
          input_content_hash, status
        ) VALUES (
          '00000000-0000-4000-8000-000000000003',
          '00000000-0000-4000-8000-000000000001',
          '00000000-0000-4000-8000-000000000002',
          'wp02-test', %s, 'succeeded'
        ) ON CONFLICT DO NOTHING
        """,
        (raw_hash,),
    )
    connection.execute(
        """
        INSERT INTO text_units (
          text_unit_uuid, unit_uuid, message_uuid, session_uuid, speaker_role,
          unit_type, unit_index, text, start_char, end_char, char_start, char_end,
          content_hash
        ) VALUES (
          '00000000-0000-4000-8000-000000000004',
          '00000000-0000-4000-8000-000000000004',
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000001',
          'user', 'sentence', 0, %s, 0, %s, 0, %s, %s
        ) ON CONFLICT DO NOTHING
        """,
        (RAW, len(RAW), len(RAW), raw_hash),
    )
    connection.execute(
        """
        INSERT INTO jakobson_analysis_runs (
          analysis_run_uuid, session_uuid, message_uuid, prompt_id,
          prompt_version, provider_type, input_hash, output_hash, status,
          warnings_jsonb
        ) VALUES (
          '00000000-0000-4000-8000-000000000006',
          '00000000-0000-4000-8000-000000000001',
          '00000000-0000-4000-8000-000000000002',
          'memorist.jakobson_sentence_analysis', '3.0', 'deterministic',
          %s, %s, 'succeeded', '[]'::jsonb
        ) ON CONFLICT DO NOTHING
        """,
        (raw_hash, raw_hash),
    )
    connection.execute(
        """
        INSERT INTO jakobson_sentence_annotations (
          annotation_uuid, analysis_run_uuid, message_uuid, unit_uuid,
          sentence_index, sentence_text, sentence_hash, sender_confidence,
          receiver_confidence, message_confidence, context_confidence,
          code_confidence, contact_channel_confidence, dominant_function,
          secondary_functions_jsonb, raw_sentence_output_jsonb
        ) VALUES (
          '00000000-0000-4000-8000-000000000007',
          '00000000-0000-4000-8000-000000000006',
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000004',
          1, %s, %s, 'high', 'high', 'high', 'high', 'high', 'high',
          'referential', '[]'::jsonb, '{}'::jsonb
        ) ON CONFLICT DO NOTHING
        """,
        (RAW, raw_hash),
    )
    connection.execute(
        """
        INSERT INTO memory_signal_routes (
          route_uuid, annotation_uuid, message_uuid, unit_uuid,
          dominant_function, secondary_functions_jsonb, route_type,
          extractor_id, priority, confidence, reason, status
        ) VALUES (
          '00000000-0000-4000-8000-000000000008',
          '00000000-0000-4000-8000-000000000007',
          '00000000-0000-4000-8000-000000000002',
          '00000000-0000-4000-8000-000000000004',
          'referential', '[]'::jsonb, 'project_context',
          'wp02-test', 1, 'high', 'test', 'ready'
        ) ON CONFLICT DO NOTHING
        """
    )
    connection.execute(
        """
        INSERT INTO memory_gate_decisions (
          gate_decision_uuid, text_unit_uuid, processing_run_uuid, decision,
          reason_codes_ijson, salience_score, persistence_score,
          actionability_score, sensitivity_score, novelty_score
        ) VALUES (
          '00000000-0000-4000-8000-000000000005',
          '00000000-0000-4000-8000-000000000004',
          '00000000-0000-4000-8000-000000000003',
          'analyze', '[]', 1, 1, 1, 0, 1
        ) ON CONFLICT DO NOTHING
        """
    )
    connection.execute(
        """
        INSERT INTO prompt_execution_runs (
          prompt_execution_uuid, prompt_id, prompt_version, stage, model_role,
          provider_type, model_name, session_uuid, message_uuid, input_hash,
          output_hash, status, warnings_ijson, created_at
        ) VALUES (
          '00000000-0000-4000-8000-000000000009',
          'memorist.semantic_candidate_analysis', '1.0',
          'semantic_candidate_analysis', 'memory_extraction', 'deterministic',
          'test', '00000000-0000-4000-8000-000000000001',
          '00000000-0000-4000-8000-000000000002',
          %s, %s, 'ok', '[]', %s
        ) ON CONFLICT DO NOTHING
        """,
        (raw_hash, raw_hash, now),
    )
    connection.commit()


def _cleanup_postgres(connection: Any, proposal_id: str) -> None:
    connection.rollback()
    connection.execute("DELETE FROM candidate_evidence WHERE candidate_uuid = %s", (proposal_id,))
    connection.execute(
        "DELETE FROM semantic_candidate_links WHERE proposal_uuid = %s",
        (proposal_id,),
    )
    connection.execute(
        """
        DELETE FROM semantic_coverage_items
        WHERE coverage_run_uuid IN (
          SELECT coverage_run_uuid
          FROM semantic_coverage_runs
          WHERE message_uuid = '00000000-0000-4000-8000-000000000002'
        )
        """
    )
    connection.execute(
        """
        DELETE FROM semantic_coverage_runs
        WHERE message_uuid = '00000000-0000-4000-8000-000000000002'
        """
    )
    connection.execute("DELETE FROM memory_candidates WHERE candidate_uuid = %s", (proposal_id,))
    for table, column, value in (
        (
            "memory_gate_decisions",
            "gate_decision_uuid",
            "00000000-0000-4000-8000-000000000005",
        ),
        (
            "memory_signal_routes",
            "route_uuid",
            "00000000-0000-4000-8000-000000000008",
        ),
        (
            "jakobson_sentence_annotations",
            "annotation_uuid",
            "00000000-0000-4000-8000-000000000007",
        ),
        (
            "jakobson_analysis_runs",
            "analysis_run_uuid",
            "00000000-0000-4000-8000-000000000006",
        ),
        (
            "prompt_execution_runs",
            "prompt_execution_uuid",
            "00000000-0000-4000-8000-000000000009",
        ),
        (
            "text_units",
            "text_unit_uuid",
            "00000000-0000-4000-8000-000000000004",
        ),
        (
            "memory_processing_runs",
            "processing_run_uuid",
            "00000000-0000-4000-8000-000000000003",
        ),
        (
            "message_versions",
            "message_uuid",
            "00000000-0000-4000-8000-000000000002",
        ),
        (
            "messages",
            "message_uuid",
            "00000000-0000-4000-8000-000000000002",
        ),
        (
            "sessions",
            "session_uuid",
            "00000000-0000-4000-8000-000000000001",
        ),
    ):
        connection.execute(f"DELETE FROM {table} WHERE {column} = %s", (value,))
    connection.commit()


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_postgres_repeated_migration_concurrent_reservation_and_real_link() -> None:
    psycopg = importlib.import_module("psycopg")
    plan, proposal, bindings = _plan()
    first = psycopg.connect(os.environ["MEMORIST_POSTGRES_DSN"])
    second = psycopg.connect(os.environ["MEMORIST_POSTGRES_DSN"])
    try:
        apply_postgres_migrations(first)
        apply_postgres_migrations(first)
        _cleanup_postgres(first, proposal.proposal_id)
        _seed_postgres(first)
        repository = PostgresSemanticCoverageRepository(first)
        repository.persist_plan(plan, bindings)
        candidate, evidence = _candidate(proposal)
        payload_hash = candidate_payload_hash(candidate, (evidence,))
        item_id = plan.items[0].coverage_item_id
        outcomes: list[dict[str, object]] = []
        failures: list[BaseException] = []

        def reserve(connection: Any) -> None:
            try:
                outcomes.append(
                    PostgresSemanticCoverageRepository(connection).reserve_candidate(
                        proposal.proposal_id, item_id, payload_hash
                    )
                )
            except BaseException as error:
                failures.append(error)

        threads = [
            threading.Thread(target=reserve, args=(first,)),
            threading.Thread(target=reserve, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not failures
        assert len(outcomes) == 2

        linked = repository.create_and_link_candidate(proposal, candidate, (evidence,))
        replay = PostgresSemanticCoverageRepository(second).create_and_link_candidate(
            proposal, candidate, (evidence,)
        )
        assert linked["state"] == "created"
        assert replay["state"] == "existing"
        row = first.execute(
            """
            SELECT proposal_uuid, candidate_uuid, state
            FROM semantic_candidate_links
            WHERE proposal_uuid = %s
            """,
            (proposal.proposal_id,),
        ).fetchone()
        assert row[0] == row[1] == proposal.proposal_id
        assert row[2] == "candidate_linked"
        with pytest.raises(SemanticCoverageIdentityConflict):
            repository.reserve_candidate(proposal.proposal_id, item_id, "0" * 64)
    finally:
        first.rollback()
        second.rollback()
        try:
            _cleanup_postgres(first, proposal.proposal_id)
        except Exception:
            first.rollback()
        first.close()
        second.close()


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_coverage_schema_parity_and_sqlite_to_postgres_copy(tmp_path: Path) -> None:
    report = build_parity_report()
    assert report["status"] == "pass"
    assert not report["missing_in_sqlite"]
    assert not report["missing_in_postgres"]

    plan, proposal, bindings = _plan()
    source_path = tmp_path / "migration-source.sqlite"
    source = connect(source_path)
    apply_migrations(source)
    _seed_authority(source)
    repository = SQLiteSemanticCoverageRepository(source)
    repository.persist_plan(plan, bindings)
    candidate, evidence = _candidate(proposal)
    repository.reserve_candidate(
        proposal.proposal_id,
        plan.items[0].coverage_item_id,
        candidate_payload_hash(candidate, (evidence,)),
    )
    repository.create_and_link_candidate(proposal, candidate, (evidence,))
    source.close()

    psycopg = importlib.import_module("psycopg")
    target = psycopg.connect(os.environ["MEMORIST_POSTGRES_DSN"])
    try:
        apply_postgres_migrations(target)
        _cleanup_postgres(target, proposal.proposal_id)
        copied = migrate_sqlite_to_postgres(source_path, os.environ["MEMORIST_POSTGRES_DSN"])[
            "copied"
        ]
        assert copied["semantic_coverage_runs"] == 1
        assert copied["semantic_coverage_items"] == 1
        assert copied["semantic_candidate_links"] == 1
        row = target.execute(
            """
            SELECT state, candidate_uuid
            FROM semantic_candidate_links
            WHERE proposal_uuid = %s
            """,
            (proposal.proposal_id,),
        ).fetchone()
        assert row == ("candidate_linked", proposal.proposal_id)
    finally:
        _cleanup_postgres(target, proposal.proposal_id)
        target.close()
