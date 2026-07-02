from __future__ import annotations


def run() -> dict[str, object]:
    return {
        "name": "privacy_smoke",
        "passed": True,
        "placeholder": True,
        "classification": "placeholder",
        "reason": (
            "Static privacy checklist marker; pytest security/governance tests are release "
            "gates."
        ),
    }
