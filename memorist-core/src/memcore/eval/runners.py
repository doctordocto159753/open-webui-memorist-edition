from __future__ import annotations

from memcore.eval.harness import run_dataset


def run_basic_smoke(dataset_path: str, output_dir: str) -> dict[str, object]:
    return run_dataset(dataset_path, output_dir=output_dir)
