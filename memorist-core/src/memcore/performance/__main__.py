from __future__ import annotations

import argparse
import json

from memcore.performance.loadgen import run_loadgen, run_perf_smoke
from memcore.performance.reports import write_performance_report


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m memcore.performance")
    parser.add_argument("command", choices=["perf-smoke", "perf-local"])
    parser.add_argument("--profile", default="lite")
    parser.add_argument("--out", default="perf-runs/latest")
    args = parser.parse_args()
    if args.command == "perf-smoke":
        report = run_perf_smoke(args.profile)
    else:
        report = run_loadgen("1000-message-import", args.profile)
    report["artifacts"] = write_performance_report(args.out, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
