from __future__ import annotations

import argparse
import json

from memcore.migrate.sqlite_to_postgres import commit, dry_run, verify


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m memcore.migrate")
    subcommands = parser.add_subparsers(dest="command", required=True)
    sqlite_to_postgres = subcommands.add_parser("sqlite-to-postgres")
    sqlite_to_postgres.add_argument("--sqlite", required=True)
    sqlite_to_postgres.add_argument("--postgres", required=True)
    mode = sqlite_to_postgres.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.command == "sqlite-to-postgres":
        if args.dry_run:
            result = dry_run(args.sqlite)
        elif args.commit:
            result = commit(args.sqlite, args.postgres)
        else:
            result = verify(args.sqlite, args.postgres)
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
