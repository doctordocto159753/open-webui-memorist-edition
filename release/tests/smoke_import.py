from __future__ import annotations


def run() -> dict[str, object]:
    return {
        "name": "import_smoke",
        "passed": True,
        "placeholder": True,
        "classification": "placeholder",
        "reason": (
            "Does not execute the import pipeline; pytest covers real import/Heritage "
            "contracts."
        ),
    }
