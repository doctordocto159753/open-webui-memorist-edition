from memcore.models import SensitivityClass
from memcore.textsemantics import Lexicon, NormalizedText, normalize_with_mapping, tokenize

# Token-boundary matching, so "tokenizer" and "the token bucket algorithm" no
# longer classify as SECRET while "rotate the access_token" still does: an
# underscore is a token boundary, so "access_token" is the two tokens "access"
# and "token". For the same reason "api key" also covers "api_key".
SECRET_LEXICON = Lexicon(
    name="secret_terms",
    phrases=(
        "api key",
        "apikey",
        "password",
        "passphrase",
        "token",
        "secret",
        "credential",
        "credentials",
        "رمز",
        "توکن",
    ),
)
SENSITIVE_LEXICON = Lexicon(
    name="sensitive_terms",
    phrases=(
        "religion",
        "religious",
        "political",
        "politics",
        "medical",
        "health",
        "diagnosis",
        "مذهب",
        "سیاسی",
        "پزشکی",
        "سلامت",
    ),
)

# "sk-" is a key prefix, not a word. Matching it as a substring flagged
# "task-force"; matching it as a token followed by a key body does not.
SECRET_KEY_PREFIXES = frozenset({"sk"})
MIN_KEY_BODY_LENGTH = 8


def classify_sensitivity(text: str) -> SensitivityClass:
    """Classify text sensitivity on token boundaries.

    Fenced code is scanned as well: a credential pasted into a code block is
    exactly the case that must not slip through as NORMAL.
    """

    normalized = normalize_with_mapping(text)
    if SECRET_LEXICON.search(normalized, include_code=True) is not None:
        return SensitivityClass.SECRET
    if _has_key_prefix(normalized):
        return SensitivityClass.SECRET
    if SENSITIVE_LEXICON.search(normalized, include_code=True) is not None:
        return SensitivityClass.SENSITIVE
    return SensitivityClass.NORMAL


def _has_key_prefix(normalized: NormalizedText) -> bool:
    tokens = tokenize(normalized, include_code=True)
    for position, token in enumerate(tokens[:-1]):
        if token.key not in SECRET_KEY_PREFIXES:
            continue
        following = tokens[position + 1]
        # One separator character between them, and a body long enough to be a
        # key rather than a hyphenated word.
        if following.raw_start == token.raw_end + 1 and len(following.key) >= MIN_KEY_BODY_LENGTH:
            return True
    return False
