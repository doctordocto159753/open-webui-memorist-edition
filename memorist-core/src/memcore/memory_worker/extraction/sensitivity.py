import re

from memcore.models import SensitivityClass
from memcore.textsemantics import Lexicon, NormalizedText, normalize_with_mapping, tokenize

# Token-boundary matching, so "tokenizer" and "the token bucket algorithm" no
# longer classify as SECRET while "rotate the access_token" still does: an
# underscore is a token boundary, so "access_token" is the two tokens "access"
# and "token". For the same reason "api key" also covers "api_key".
#
# Plurals and inflections are listed explicitly. The previous substring matcher
# caught them for free, and dropping them would silently downgrade
# "do not commit secrets to git" to NORMAL -- a fail-open change, since SECRET
# is what forces a candidate to REJECTED.
SECRET_LEXICON = Lexicon(
    name="secret_terms",
    phrases=(
        "api key",
        "api keys",
        "apikey",
        "apikeys",
        "password",
        "passwords",
        "passphrase",
        "passphrases",
        "token",
        "tokens",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "bearer",
        "private key",
        "private keys",
        "رمز",
        "رمزها",
        "توکن",
        "توکن‌ها",
        "کلیدها",
    ),
)
SENSITIVE_LEXICON = Lexicon(
    name="sensitive_terms",
    phrases=(
        "religion",
        "religions",
        "religious",
        "political",
        "politics",
        "medical",
        "health",
        "phone",
        "telephone",
        "mobile",
        "email",
        "e-mail",
        "address",
        "home address",
        "diagnosis",
        "diagnoses",
        "مذهب",
        "مذاهب",
        "سیاسی",
        "پزشکی",
        "سلامت",
        "تلفن",
        "موبایل",
        "ایمیل",
        "آدرس",
        "نشانی",
    ),
)

# "sk-" is a key prefix, not a word. Matching it as a substring flagged
# "task-force"; anchoring it to a whole token does not.
SECRET_KEY_PREFIXES = frozenset({"sk"})
MIN_KEY_BODY_LENGTH = 8
KEY_BODY_CHARACTERS = "-_"
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}={0,2}")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.IGNORECASE,
)
_JWT_VALUE = re.compile(
    r"(?<![a-z0-9_-])[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}(?![a-z0-9_-])",
    re.IGNORECASE,
)
_EMAIL_VALUE = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])",
    re.UNICODE,
)
_PHONE_VALUE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{8,}\d)(?!\w)")


def classify_sensitivity(text: str) -> SensitivityClass:
    """Classify text sensitivity on token boundaries.

    Fenced code is scanned as well: a credential pasted into a code block is
    exactly the case that must not slip through as NORMAL.
    """

    normalized = normalize_with_mapping(text)
    if (
        _BEARER_VALUE.search(normalized.text)
        or _PRIVATE_KEY_BLOCK.search(text)
        or _JWT_VALUE.search(normalized.text)
    ):
        return SensitivityClass.SECRET
    if SECRET_LEXICON.search(normalized, include_code=True) is not None:
        return SensitivityClass.SECRET
    if _has_key_prefix(normalized):
        return SensitivityClass.SECRET
    if _EMAIL_VALUE.search(normalized.text) or _PHONE_VALUE.search(normalized.text):
        return SensitivityClass.SENSITIVE
    if SENSITIVE_LEXICON.search(normalized, include_code=True) is not None:
        return SensitivityClass.SENSITIVE
    return SensitivityClass.NORMAL


def _has_key_prefix(normalized: NormalizedText) -> bool:
    """Detect an ``sk-`` credential without matching hyphenated words.

    The body is measured over the whole run after the prefix rather than the
    next token alone, because real keys are multi-segment:
    ``sk-proj-AbCd...`` and ``sk-ant-api03-...`` both have a short first
    segment. Requiring one long token missed every modern key format.
    """

    text = normalized.text
    for token in tokenize(normalized, include_code=True):
        if token.key not in SECRET_KEY_PREFIXES:
            continue
        if token.end >= len(text) or text[token.end] not in KEY_BODY_CHARACTERS:
            continue
        cursor = token.end + 1
        while cursor < len(text) and (
            text[cursor].isalnum() or text[cursor] in KEY_BODY_CHARACTERS
        ):
            cursor += 1
        body = text[token.end + 1 : cursor]
        if len(body.replace("-", "").replace("_", "")) >= MIN_KEY_BODY_LENGTH:
            return True
    return False
