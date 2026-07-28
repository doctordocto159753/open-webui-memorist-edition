"""PRD-02 WP01: polarity separated from confidence, and stored as its own field.

Confidence answers "did we extract this correctly?". Polarity answers "does the
claim assert or deny?". A negated claim asserted just as plainly as its positive
equivalent must score the same confidence and differ only in polarity.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memcore.memory_worker.consolidation.conflicts import appears_contradictory
from memcore.memory_worker.extraction.confidence import derive_confidence
from memcore.memory_worker.semantic import (
    CandidateAuthorityContext,
    CandidateServiceInput,
    CanonicalRouteReference,
    LinguisticCandidateComplements,
    build_candidate_draft,
)
from memcore.memory_worker.semantic.candidate_service import read_modality_polarity
from memcore.models import (
    CandidateStatus,
    Explicitness,
    GateDecisionValue,
    MemoryCandidate,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
    MemoryVersion,
    Polarity,
    SourceAuthority,
)
from memcore.storage.migrations import apply_migrations, migration_files
from memcore.textsemantics import NORMALIZATION_CONTRACT_VERSION

CANDIDATE_UUID = "00000000-0000-4000-8000-0000000000c1"
RUN_UUID = "00000000-0000-4000-8000-000000000001"
UNIT_UUID = "00000000-0000-4000-8000-000000000002"
MEMORY_UUID = "00000000-0000-4000-8000-000000000003"


def _authority() -> CandidateAuthorityContext:
    return CandidateAuthorityContext(
        gate_decision=GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
        requires_high_confidence_pass=True,
        selected_route=CanonicalRouteReference(
            route_uuid="route-1",
            annotation_uuid="annotation-1",
            route_type=MemorySignalRouteType.TASK_CONSTRAINT,
            route_status=MemorySignalRouteStatus.READY,
            priority=90,
        ),
        analysis_run_uuid="analysis-1",
        prompt_execution_uuid="execution-1",
    )


def _draft_input(text: str, polarity: Polarity) -> CandidateServiceInput:
    return CandidateServiceInput(
        message_uuid="message-1",
        message_role="user",
        text_unit_uuid="unit-1",
        text=text,
        start_char=0,
        end_char=len(text),
        processing_run_uuid="run-1",
        authority=_authority(),
        complements=LinguisticCandidateComplements(
            analysis_uuid="analysis-1",
            polarity=polarity,
        ),
    )


# --------------------------------------------------------------------------
# Confidence no longer penalises negation
# --------------------------------------------------------------------------


def test_negated_and_affirmed_claims_receive_equal_confidence() -> None:
    affirmed = build_candidate_draft(
        _draft_input("Always use the release checklist.", Polarity.AFFIRMED)
    )
    negated = build_candidate_draft(
        _draft_input("Never use the release checklist.", Polarity.NEGATED)
    )

    assert affirmed is not None and negated is not None
    assert affirmed.confidence == negated.confidence
    assert affirmed.polarity is Polarity.AFFIRMED
    assert negated.polarity is Polarity.NEGATED


def test_polarity_is_not_an_input_to_the_confidence_formula() -> None:
    scores = {
        build_candidate_draft(_draft_input("Use the checklist.", polarity)).confidence  # type: ignore[union-attr]
        for polarity in Polarity
    }

    assert len(scores) == 1, "confidence must not vary with polarity"


def test_remaining_confidence_coefficients_are_unchanged() -> None:
    """Only the negation penalty was removed; every other weight is preserved."""

    draft = build_candidate_draft(_draft_input("Use the checklist.", Polarity.AFFIRMED))

    assert draft is not None
    # 0.45 base + 0.25 user_explicit + 0.15 explicit + 0.13 evidence/temporal.
    assert draft.confidence == pytest.approx(0.98)


def test_derive_confidence_no_longer_accepts_a_negation_penalty() -> None:
    baseline = derive_confidence(
        SourceAuthority.USER_EXPLICIT,
        Explicitness.EXPLICIT,
        evidence_complete=True,
        temporal_clear=True,
    )

    assert baseline == pytest.approx(0.98)
    with pytest.raises(TypeError):
        derive_confidence(  # type: ignore[call-arg]
            SourceAuthority.USER_EXPLICIT,
            Explicitness.EXPLICIT,
            True,
            True,
            negated=True,
        )


def test_needs_review_and_rejected_ceilings_are_preserved() -> None:
    ceiling = build_candidate_draft(_draft_input("The api key is AKIA-secret.", Polarity.AFFIRMED))

    assert ceiling is not None
    assert ceiling.status is CandidateStatus.REJECTED
    assert ceiling.confidence <= 0.35


# --------------------------------------------------------------------------
# Polarity is carried and recorded
# --------------------------------------------------------------------------


def test_draft_metadata_records_polarity_and_contract_version() -> None:
    draft = build_candidate_draft(_draft_input("Never ship on Friday.", Polarity.NEGATED))

    assert draft is not None
    assert draft.metadata["polarity"] == "negated"
    assert draft.metadata["normalization_contract_version"] == NORMALIZATION_CONTRACT_VERSION


def test_complements_expose_a_boolean_view_without_owning_it() -> None:
    assert LinguisticCandidateComplements(polarity=Polarity.NEGATED).negated is True
    assert LinguisticCandidateComplements(polarity=Polarity.AFFIRMED).negated is False
    assert LinguisticCandidateComplements().polarity is Polarity.UNKNOWN
    assert LinguisticCandidateComplements().negated is False


@pytest.mark.parametrize(
    ("modality", "expected"),
    [
        ({"polarity": "negated"}, Polarity.NEGATED),
        ({"polarity": "affirmed"}, Polarity.AFFIRMED),
        ({"polarity": "unknown"}, Polarity.UNKNOWN),
        ({"polarity": "garbage"}, Polarity.UNKNOWN),
        # Rows written before polarity existed carry only the older boolean.
        ({"negated": True}, Polarity.NEGATED),
        ({"negated": False}, Polarity.AFFIRMED),
        # Neither key recorded: nothing is invented.
        ({}, Polarity.UNKNOWN),
        ({"hypothetical": True}, Polarity.UNKNOWN),
        # A newer payload wins over the legacy boolean.
        ({"polarity": "affirmed", "negated": True}, Polarity.AFFIRMED),
    ],
)
def test_historical_modality_payloads_read_back_truthfully(
    modality: dict[str, object], expected: Polarity
) -> None:
    assert read_modality_polarity(modality, allow_legacy_unstamped=True) is expected


def test_domain_models_default_polarity_to_unknown() -> None:
    candidate = MemoryCandidate(
        processing_run_uuid=RUN_UUID,
        text_unit_uuid=UNIT_UUID,
        candidate_type="fact",  # type: ignore[arg-type]
        normalized_text="x",
        source_authority=SourceAuthority.USER_EXPLICIT,
        explicitness=Explicitness.EXPLICIT,
        confidence=0.9,
        importance=0.5,
    )
    version = MemoryVersion(
        memory_uuid=MEMORY_UUID,
        version_number=1,
        normalized_text="x",
        confidence=0.9,
        importance=0.5,
        change_operation="add",  # type: ignore[arg-type]
    )

    assert candidate.polarity is Polarity.UNKNOWN
    assert version.polarity is Polarity.UNKNOWN


# --------------------------------------------------------------------------
# Contradiction detection
# --------------------------------------------------------------------------


def test_contradiction_uses_polarity_not_substrings() -> None:
    assert appears_contradictory("We deploy on Friday.", "We never deploy on Friday.") is True
    assert appears_contradictory("We deploy on Friday.", "We deploy on Monday.") is False
    # The old substring markers fired inside these words.
    assert appears_contradictory("Send a notification.", "Send a reminder.") is False
    assert appears_contradictory("Nevertheless we ship.", "We ship.") is False


def test_contradiction_needs_two_decided_polarities() -> None:
    assert appears_contradictory("", "We never ship.") is False
    assert appears_contradictory("We never ship.", "") is False


# --------------------------------------------------------------------------
# Migration and historical compatibility
# --------------------------------------------------------------------------


def _migrations_through(version: int, tmp_path: Path) -> Path:
    """A migrations directory containing only files up to ``version``."""

    source = Path(__file__).resolve().parents[1] / "migrations"
    staged = tmp_path / f"through-{version}"
    staged.mkdir()
    for path in migration_files(source):
        if int(path.name[:4]) <= version:
            (staged / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return staged


def test_polarity_migration_is_additive_over_an_existing_database(tmp_path: Path) -> None:
    """An installed store upgrades in place; historical rows read as unknown."""

    database = tmp_path / "existing.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    apply_migrations(connection, _migrations_through(35, tmp_path))

    columns = {row["name"] for row in connection.execute("PRAGMA table_info(memory_candidates)")}
    assert "polarity" not in columns, "fixture must start before the polarity migration"

    connection.execute(
        """
        INSERT INTO memory_candidates (
          candidate_uuid, processing_run_uuid, text_unit_uuid, candidate_type,
          normalized_text, source_authority, explicitness, confidence,
          importance, created_at
        ) VALUES (?,?,?,'fact','historic','user_explicit',
                  'explicit',0.93,0.5,'2026-01-01T00:00:00Z')
        """,
        (CANDIDATE_UUID, RUN_UUID, UNIT_UUID),
    )
    connection.commit()

    apply_migrations(connection, _migrations_through(36, tmp_path))
    row = connection.execute(
        "SELECT confidence, polarity FROM memory_candidates WHERE candidate_uuid = ?",
        (CANDIDATE_UUID,),
    ).fetchone()
    assert row["polarity"] == "unknown", "historical polarity must not be invented"
    assert row["confidence"] == pytest.approx(0.93), "no historical confidence is recalculated"

    # The upgraded row still validates against the current domain model.
    assert (
        MemoryCandidate.model_validate(
            {
                "candidate_uuid": CANDIDATE_UUID,
                "processing_run_uuid": RUN_UUID,
                "text_unit_uuid": UNIT_UUID,
                "candidate_type": "fact",
                "normalized_text": "historic",
                "source_authority": "user_explicit",
                "explicitness": "explicit",
                "confidence": 0.93,
                "importance": 0.5,
                "polarity": row["polarity"],
            }
        ).polarity
        is Polarity.UNKNOWN
    )


def test_polarity_migration_is_idempotent(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "repeat.sqlite3")
    connection.row_factory = sqlite3.Row
    for _ in range(3):
        apply_migrations(connection)

    for table in ("memory_candidates", "memory_versions"):
        columns = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
        assert columns.count("polarity") == 1


def test_sqlite_and_postgres_migrations_declare_the_same_polarity_columns() -> None:
    root = Path(__file__).resolve().parents[1]
    sqlite_sql = (root / "migrations" / "0036_claim_polarity.sql").read_text(encoding="utf-8")
    postgres_sql = (
        root / "src" / "memcore" / "storage" / "postgres" / "migrations" / "0023_claim_polarity.sql"
    ).read_text(encoding="utf-8")

    for table in ("memory_candidates", "memory_versions"):
        assert f"ALTER TABLE {table} ADD COLUMN polarity" in sqlite_sql
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS polarity" in postgres_sql
    assert "DEFAULT 'unknown'" in sqlite_sql
    assert "DEFAULT 'unknown'" in postgres_sql
    assert "DROP" not in sqlite_sql.upper()
    assert "DROP" not in postgres_sql.upper()
