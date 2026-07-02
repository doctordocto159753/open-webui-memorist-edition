from memcore.models import SensitivityClass

SECRET_TERMS = ("api_key", "api key", "password", "token", "secret", "credential", "sk-")
SENSITIVE_TERMS = (
    "religion",
    "political",
    "medical",
    "health",
    "diagnosis",
    "مذهب",
    "سیاسی",
    "پزشکی",
    "سلامت",
)


def classify_sensitivity(text: str) -> SensitivityClass:
    lowered = text.lower()
    if any(term in lowered for term in SECRET_TERMS):
        return SensitivityClass.SECRET
    if any(term in lowered for term in SENSITIVE_TERMS):
        return SensitivityClass.SENSITIVE
    return SensitivityClass.NORMAL
