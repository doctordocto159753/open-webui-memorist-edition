import re

from memcore.models import MemoryCandidate


def canonical_key_for_candidate(candidate: MemoryCandidate) -> str:
    subject = _slug(candidate.subject_key or "unknown")
    predicate = _slug(candidate.predicate or candidate.candidate_type.value)
    return f"{candidate.candidate_type.value}:{subject}:{predicate}"


def _slug(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip().lower())
    return re.sub(r"[^a-z0-9_\-\u0600-\u06ff]+", "", normalized)
