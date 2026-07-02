from __future__ import annotations

import argparse
import json

from memcore.reliability.consistency.checker import run_consistency_check
from memcore.reliability.consistency.repair import safe_repair
from memcore.reliability.consistency.report import write_consistency_report
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--db-path", required=True)
    check_parser.add_argument("--json-output")

    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--db-path", required=True)

    args = parser.parse_args()
    connection = connect(args.db_path)
    try:
        apply_migrations(connection)
        if args.command == "check":
            report = run_consistency_check(connection)
            if args.json_output:
                report["report_paths"] = write_consistency_report(report, args.json_output)
            print(json.dumps(report, indent=2))
        else:
            print(json.dumps(safe_repair(connection), indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()

