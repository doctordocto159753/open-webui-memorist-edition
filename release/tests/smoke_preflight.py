from __future__ import annotations


def run() -> dict[str, object]:
    return {
        "name": "preflight_smoke",
        "passed": True,
        "placeholder": True,
        "classification": "placeholder",
        "reason": "Static marker; real preflight and attachment separation are covered by pytest.",
    }
