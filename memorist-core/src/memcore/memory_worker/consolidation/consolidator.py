import sqlite3

from memcore.memory_worker.consolidation.canonical_keys import canonical_key_for_candidate
from memcore.memory_worker.consolidation.operations import change_operation_for
from memcore.memory_worker.consolidation.resolver import DeterministicResolver
from memcore.models import (
    ConsolidationOperation,
    Memory,
    MemoryCandidate,
    MemoryConsolidationDecision,
    MemoryVersion,
    SensitivityClass,
    Session,
    utc_now,
)
from memcore.repositories.domain import RepositoryError, SessionRepository
from memcore.repositories.memory_worker import MemoryCandidateRepository, MemoryStoreRepository
from memcore.validators.ijson import dump_ijson, load_ijson


class MemoryConsolidator:
    def __init__(
        self,
        connection: sqlite3.Connection,
        resolver: DeterministicResolver | None = None,
    ) -> None:
        self.connection = connection
        self.resolver = resolver or DeterministicResolver()
        self.candidates = MemoryCandidateRepository(connection)
        self.store = MemoryStoreRepository(connection)

    def consolidate_candidate(self, candidate_uuid: str) -> MemoryConsolidationDecision:
        existing_decision = self.store.get_consolidation_decision(candidate_uuid)
        if existing_decision is not None:
            return existing_decision

        candidate = self.candidates.get_candidate(candidate_uuid)
        if candidate is None:
            raise RepositoryError(f"Candidate not found: {candidate_uuid}")
        evidence = self.candidates.list_evidence(candidate_uuid)
        if not evidence:
            return self.store.record_decision_only(
                candidate_uuid,
                ConsolidationOperation.REJECT,
                ["missing_evidence"],
            )

        session = self._session_for_candidate(candidate)
        scope_type, scope_uuid = _scope_for_session(session)
        canonical_key = canonical_key_for_candidate(candidate)
        memory = self.store.find_by_canonical_key(scope_type, scope_uuid, canonical_key)
        current_version = self._current_version(memory) if memory is not None else None
        resolution = self.resolver.resolve(candidate, current_version)

        if resolution.operation in {
            ConsolidationOperation.REJECT,
            ConsolidationOperation.MANUAL_REVIEW,
            ConsolidationOperation.NOOP,
        }:
            return self.store.record_decision_only(
                candidate_uuid,
                resolution.operation,
                resolution.reason_codes,
            )

        if memory is None:
            memory = Memory(
                canonical_key=canonical_key,
                memory_type=candidate.candidate_type.value,
                scope_type=scope_type,
                scope_uuid=scope_uuid,
                sensitivity_class=SensitivityClass(candidate.sensitivity_class),
            )
            version = self._version_for(candidate, memory.memory_uuid, 1, resolution.operation)
            return self.store.create_memory_with_version(
                memory,
                version,
                candidate.candidate_uuid,
                resolution.reason_codes,
                resolution.operation,
                evidence,
            )

        version_number = 1 if current_version is None else current_version.version_number + 1

        if resolution.operation is ConsolidationOperation.REINFORCE and current_version is not None:
            return self.store.record_reinforcement(
                memory,
                current_version,
                candidate.candidate_uuid,
                resolution.reason_codes,
                evidence,
            )

        if resolution.operation is ConsolidationOperation.CONTRADICT:
            return self.store.record_decision_only(
                candidate.candidate_uuid,
                ConsolidationOperation.CONTRADICT,
                resolution.reason_codes,
            )

        version = self._version_for(
            candidate, memory.memory_uuid, version_number, resolution.operation
        )
        return self.store.create_version_for_memory(
            memory,
            version,
            candidate.candidate_uuid,
            resolution.reason_codes,
            resolution.operation,
            evidence,
            close_previous=resolution.operation is ConsolidationOperation.SUPERSEDE,
        )

    def _session_for_candidate(self, candidate: MemoryCandidate) -> Session:
        row = self.connection.execute(
            """
            SELECT s.*
            FROM sessions s
            JOIN text_units tu ON tu.session_uuid = s.session_uuid
            WHERE tu.text_unit_uuid = ?
            """,
            (candidate.text_unit_uuid,),
        ).fetchone()
        if row is None:
            raise RepositoryError(f"Session not found for candidate: {candidate.candidate_uuid}")
        session = SessionRepository(self.connection).get_session(str(row["session_uuid"]))
        if session is None:
            raise RepositoryError(f"Session not found: {row['session_uuid']}")
        return session

    def _current_version(self, memory: Memory | None) -> MemoryVersion | None:
        if memory is None or memory.current_version_uuid is None:
            return None
        row = self.connection.execute(
            "SELECT * FROM memory_versions WHERE memory_version_uuid = ?",
            (memory.current_version_uuid,),
        ).fetchone()
        return MemoryVersion.model_validate(dict(row)) if row is not None else None

    def _version_for(
        self,
        candidate: MemoryCandidate,
        memory_uuid: str,
        version_number: int,
        operation: ConsolidationOperation,
    ) -> MemoryVersion:
        return MemoryVersion(
            memory_uuid=memory_uuid,
            version_number=version_number,
            normalized_text=candidate.normalized_text,
            value_ijson=candidate.object_ijson,
            confidence=candidate.confidence,
            importance=candidate.importance,
            valid_from=candidate.valid_from,
            valid_until=candidate.valid_until,
            transaction_from=utc_now(),
            source_candidate_uuid=candidate.candidate_uuid,
            change_operation=change_operation_for(operation),
            change_reason_ijson=dump_ijson(
                {
                    "operation": operation.value,
                    "candidate_status": candidate.status.value,
                    "rejection_reason_codes": load_ijson(candidate.rejection_reason_codes_ijson)
                    if candidate.rejection_reason_codes_ijson
                    else [],
                }
            ),
        )


def _scope_for_session(session: Session) -> tuple[str, str | None]:
    if session.project_uuid is not None:
        return "project", session.project_uuid
    if session.workspace_uuid is not None:
        return "workspace", session.workspace_uuid
    return "session", session.session_uuid
