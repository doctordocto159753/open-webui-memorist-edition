from __future__ import annotations

import argparse

from memcore.config import get_settings
from memcore.graph.service import GraphProjectionService


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m memcore.graph")
    subcommands = parser.add_subparsers(dest="command", required=True)
    rebuild = subcommands.add_parser("rebuild")
    rebuild.add_argument("--store", choices=["postgres"], required=True)
    args = parser.parse_args()

    if args.command == "rebuild":
        print(GraphProjectionService(get_settings()).rebuild())


if __name__ == "__main__":
    main()
