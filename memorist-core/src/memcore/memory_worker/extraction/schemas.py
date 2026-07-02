from pydantic import BaseModel

from memcore.models import CandidateEvidence, MemoryCandidate


class ExtractedCandidate(BaseModel):
    candidate: MemoryCandidate
    evidence: list[CandidateEvidence]
