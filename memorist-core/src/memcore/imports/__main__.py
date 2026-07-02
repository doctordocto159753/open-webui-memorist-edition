import argparse
import json

from memcore.imports.fixtures import generate_openwebui_heavy_fixture
from memcore.imports.service import ImportService
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("archive")
    inspect_parser.add_argument("--db-path", default=":memory:")
    inspect_parser.add_argument("--object-store", default="./data/objects")

    fixture_parser = subparsers.add_parser("generate-heavy")
    fixture_parser.add_argument("output")
    fixture_parser.add_argument("--conversations", type=int, default=1_000)
    fixture_parser.add_argument("--messages", type=int, dest="legacy_messages")
    fixture_parser.add_argument("--messages-per-conversation", type=int)
    fixture_parser.add_argument("--branches", type=int, default=1)
    fixture_parser.add_argument("--duplicate-every", type=int, default=0)
    fixture_parser.add_argument("--malformed-every", type=int, default=0)
    fixture_parser.add_argument("--malicious-content", action="store_true")
    args = parser.parse_args()
    if args.command == "generate-heavy":
        result = generate_openwebui_heavy_fixture(
            args.output,
            conversations=args.conversations,
            messages_per_conversation=args.messages_per_conversation
            or args.legacy_messages
            or 2,
            branches=args.branches,
            duplicate_every=args.duplicate_every,
            malformed_every=args.malformed_every,
            malicious_content=args.malicious_content,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    connection = connect(args.db_path)
    try:
        apply_migrations(connection)
        service = ImportService(connection, args.object_store)
        run = service.upload(args.archive, mode="inspect")
        inspected = service.inspect(run["import_run_uuid"])
        print(json.dumps(inspected, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
