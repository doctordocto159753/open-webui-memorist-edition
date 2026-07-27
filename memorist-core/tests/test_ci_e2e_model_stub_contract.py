from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from memcore.memory_worker.prompts.contracts import JAKOBSON_V3_CONTRACT


def _load_stub_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    stub_path = repo_root / "tests" / "e2e" / "model-stub" / "server.py"
    spec = importlib.util.spec_from_file_location("memorist_e2e_model_stub", stub_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_e2e_model_stub_emits_the_certified_jakobson_v3_contract() -> None:
    module = _load_stub_module()
    build_output = cast(
        Callable[[dict[str, Any]], dict[str, Any]],
        module.__dict__["_jakobson_output"],
    )

    output = build_output(
        {
            "sentences": [
                {
                    "id": 1,
                    "text": "My dog is named Alpha and her preferred food is chicken.",
                }
            ]
        }
    )

    assert output["prompt_version"] == "3.0"
    assert output["sentence_count"] == 1
    assert output["items"][0]["text"].startswith("My dog is named Alpha")
    assert "sentences" not in output
    assert JAKOBSON_V3_CONTRACT.validate(output) == []
