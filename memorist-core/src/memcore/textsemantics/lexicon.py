"""Named token/phrase lexicons.

A ``Lexicon`` is the token-aware replacement for an alternation regex. It keeps
the same ``search`` shape call sites already use, but a hit is a whole-token
match carrying exact raw evidence rather than an arbitrary substring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from memcore.textsemantics.normalize import NormalizedText
from memcore.textsemantics.tokens import LexicalMatch, find_any_phrase


@dataclass(frozen=True)
class Lexicon:
    """An ordered set of phrases matched on token boundaries."""

    name: str
    phrases: tuple[str, ...] = field(default_factory=tuple)

    def search(
        self,
        text: NormalizedText | str,
        *,
        include_code: bool = False,
    ) -> LexicalMatch | None:
        """Leftmost, longest whole-token hit, or ``None``."""

        return find_any_phrase(text, self.phrases, include_code=include_code)

    def matches(self, text: NormalizedText | str, *, include_code: bool = False) -> bool:
        return self.search(text, include_code=include_code) is not None
