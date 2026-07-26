"""Versioned normalization contract for the shared text-semantics service.

The contract is data, not behaviour. It records exactly which normalization
rules produced a normalized string so downstream consumers (content
fingerprints, lexical caches, evidence audits) can detect a rule change and
invalidate derived artifacts deterministically instead of silently comparing
values produced under different rules.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

NORMALIZATION_CONTRACT_VERSION = "memorist.text.normalization.v1"


class ZwnjPolicy(StrEnum):
    """How U+200C ZERO WIDTH NON-JOINER is treated inside prose.

    ``BOUNDARY`` emits a single space, so ``توسعه‌دهنده`` tokenizes exactly the
    same way as ``توسعه دهنده``. ``REMOVE`` deletes it, joining the morphemes
    into one token. ``BOUNDARY`` is the default because Persian writers use the
    ZWNJ and a plain space interchangeably.
    """

    BOUNDARY = "boundary"
    REMOVE = "remove"


@dataclass(frozen=True)
class NormalizationContract:
    """Immutable description of one normalization rule set.

    Every field is part of the fingerprint. Adding, removing, or flipping a
    field changes the fingerprint, which is the signal downstream packages use
    to invalidate anything derived from normalized text.
    """

    version: str = NORMALIZATION_CONTRACT_VERSION
    unicode_form: str = "NFC"
    unify_persian_letters: bool = True
    remove_arabic_diacritics: bool = True
    zwnj_policy: ZwnjPolicy = ZwnjPolicy.BOUNDARY
    normalize_digits: bool = True
    lowercase_letters: bool = True
    compress_whitespace: bool = True
    preserve_fenced_code: bool = True

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "version": self.version,
            "unicode_form": self.unicode_form,
            "unify_persian_letters": self.unify_persian_letters,
            "remove_arabic_diacritics": self.remove_arabic_diacritics,
            "zwnj_policy": str(self.zwnj_policy.value),
            "normalize_digits": self.normalize_digits,
            "lowercase_letters": self.lowercase_letters,
            "compress_whitespace": self.compress_whitespace,
            "preserve_fenced_code": self.preserve_fenced_code,
        }

    @property
    def fingerprint(self) -> str:
        """Stable digest of the full rule set.

        Downstream content fingerprints should mix this in so that a future
        normalization change invalidates them instead of producing a false
        "unchanged" comparison against text normalized under older rules.
        """

        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_NORMALIZATION_CONTRACT = NormalizationContract()
