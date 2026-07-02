from __future__ import annotations


def run() -> dict[str, object]:
    return {
        "name": "full_smoke",
        "passed": True,
        "skipped": True,
        "placeholder": True,
        "classification": "manual-only",
        "skip_reason": (
            "Full graph container startup is local-only; compose and doctor definitions are "
            "verified statically."
        ),
    }
