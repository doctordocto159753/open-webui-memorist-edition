from __future__ import annotations

import argparse

from memcore.storage.postgres.parity import parity_report_json


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m memcore.storage.postgres")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("parity-report")
    args = parser.parse_args()

    if args.command == "parity-report":
        print(parity_report_json())


if __name__ == "__main__":
    main()
