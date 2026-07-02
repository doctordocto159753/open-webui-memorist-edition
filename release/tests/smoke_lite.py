from __future__ import annotations


def run() -> dict[str, object]:
    return {
        "name": "lite_smoke",
        "passed": True,
        "placeholder": True,
        "classification": "placeholder",
        "reason": (
            "Static summary only; real gates are pytest, Open WebUI contract tests, "
            "and scanner."
        ),
    }
