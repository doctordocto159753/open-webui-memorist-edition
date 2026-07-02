from __future__ import annotations

import argparse
import json
from pathlib import Path

from memcore.eval.harness import run_dataset
from memcore.validators.ijson import load_ijson


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m memcore.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--dataset", required=True)
    run.add_argument("--out", default="eval-runs/latest")
    report = sub.add_parser("report")
    report.add_argument("--run-id", required=True)
    report.add_argument("--dir", default="eval-runs/latest")
    args = parser.parse_args()
    if args.command == "run":
        result = run_dataset(args.dataset, output_dir=args.out)
        print(json.dumps(result, indent=2))
    else:
        path = Path(args.dir) / "eval-report.ijson"
        payload = load_ijson(path.read_text(encoding="utf-8"))
        if payload.get("run_id") != args.run_id:
            raise SystemExit("run-id does not match report")
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
