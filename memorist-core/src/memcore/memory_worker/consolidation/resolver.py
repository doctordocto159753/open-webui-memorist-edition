from pydantic import BaseModel

from memcore.memory_worker.consolidation.conflicts import polarities_contradict
from memcore.models import CandidateStatus, ConsolidationOperation, MemoryCandidate, MemoryVersion


class Resolution(BaseModel):
    operation: ConsolidationOperation
    reason_codes: list[str]


class DeterministicResolver:
    def resolve(
        self,
        candidate: MemoryCandidate,
        current_version: MemoryVersion | None,
    ) -> Resolution:
        if candidate.status is CandidateStatus.REJECTED:
            return Resolution(
                operation=ConsolidationOperation.REJECT, reason_codes=["candidate_rejected"]
            )
        if candidate.status is CandidateStatus.NEEDS_REVIEW:
            return Resolution(
                operation=ConsolidationOperation.MANUAL_REVIEW,
                reason_codes=["candidate_needs_review"],
            )
        if candidate.predicate == "forget_request":
            return Resolution(
                operation=ConsolidationOperation.MANUAL_REVIEW,
                reason_codes=["forget_requires_authorized_workflow"],
            )
        if current_version is None:
            return Resolution(
                operation=ConsolidationOperation.ADD, reason_codes=["no_existing_memory"]
            )
        if current_version.normalized_text == candidate.normalized_text:
            return Resolution(
                operation=ConsolidationOperation.REINFORCE, reason_codes=["same_value"]
            )
        if polarities_contradict(current_version.polarity, candidate.polarity):
            return Resolution(
                operation=ConsolidationOperation.CONTRADICT, reason_codes=["conflicting_value"]
            )
        return Resolution(operation=ConsolidationOperation.SUPERSEDE, reason_codes=["newer_value"])
