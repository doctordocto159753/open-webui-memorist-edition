from __future__ import annotations

import argparse
import json

from memcore.config import get_settings
from memcore.reliability.backup import backup_sqlite
from memcore.reliability.consistency import run_consistency_check, safe_repair
from memcore.reliability.recovery import recover_interrupted_operations
from memcore.reliability.storage_maintenance import secure_delete_check, vacuum, wal_checkpoint
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m memcore.reliability")
    parser.add_argument(
        "command",
        choices=[
            "check",
            "repair",
            "backup",
            "vacuum",
            "wal-checkpoint",
            "secure-delete-check",
            "recover",
        ],
    )
    parser.add_argument("--db-path")
    parser.add_argument("--out")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    db_path = args.db_path or settings.db_path
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        if args.command == "check":
            result = run_consistency_check(connection)
        elif args.command == "repair":
            result = safe_repair(connection)
        elif args.command == "backup":
            if not args.out:
                raise SystemExit("--out is required for backup")
            result = backup_sqlite(connection, args.out)
        elif args.command == "vacuum":
            result = vacuum(connection)
        elif args.command == "wal-checkpoint":
            result = wal_checkpoint(connection)
        elif args.command == "secure-delete-check":
            result = secure_delete_check(connection)
        else:
            result = recover_interrupted_operations(connection, apply=args.yes)
        print(json.dumps(result, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
