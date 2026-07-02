from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from memcore.config import get_settings  # noqa: E402
from memcore.imports.fixtures import generate_openwebui_heavy_fixture  # noqa: E402
from memcore.imports.service import ImportService  # noqa: E402
from memcore.models import CreatorType, MessageRole  # noqa: E402
from memcore.openwebui.commands import CaptureOpenWebUIMessageCommand  # noqa: E402
from memcore.reliability.consistency import run_consistency_check  # noqa: E402
from memcore.repositories import (  # noqa: E402
    MessageRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.storage.migrations import apply_migrations  # noqa: E402
from memcore.storage.sqlite import connect  # noqa: E402
from memcore.storage.write_actor import get_write_actor  # noqa: E402
from memcore.storage.write_commands.import_commands import commit_import_via_actor  # noqa: E402

MODE_DEFAULTS: dict[str, dict[str, int]] = {
    "ci-small": {"conversations": 100, "messages_per_conversation": 2, "branches": 1},
    "small-heavy": {"conversations": 1_000, "messages_per_conversation": 2, "branches": 2},
    "local-heavy": {"conversations": 10_000, "messages_per_conversation": 2, "branches": 2},
}


def run() -> dict[str, Any]:
    return run_smoke(["--mode", "ci-small"])


def main(argv: list[str] | None = None) -> int:
    result = run_smoke(argv)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] or result.get("skipped") else 1


def run_smoke(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["ci-small", "small-heavy", "local-heavy"],
        default="ci-small",
    )
    parser.add_argument("--conversations", type=int)
    parser.add_argument("--messages", type=int, dest="legacy_messages")
    parser.add_argument("--messages-per-conversation", type=int)
    parser.add_argument("--branches", type=int)
    parser.add_argument("--duplicates", type=int, default=0)
    parser.add_argument("--malicious-content", action="store_true")
    parser.add_argument("--max-seconds", type=int)
    parser.add_argument("--report-out")
    parser.add_argument("--skip", action="store_true")
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args(argv)
    if args.skip:
        result = _base_result(args.mode)
        result.update(
            {
                "passed": False,
                "skipped": True,
                "skip_reason": "explicit --skip",
            }
        )
        _write_report(args.report_out, result)
        return result

    defaults = MODE_DEFAULTS[args.mode]
    conversations = args.conversations or int(
        os.environ.get("MEMORIST_HEAVY_CONVERSATIONS", defaults["conversations"])
    )
    messages = (
        args.messages_per_conversation
        or args.legacy_messages
        or int(
            os.environ.get(
                "MEMORIST_HEAVY_MESSAGES_PER_CONVERSATION",
                defaults["messages_per_conversation"],
            )
        )
    )
    branches = args.branches or int(os.environ.get("MEMORIST_HEAVY_BRANCHES", defaults["branches"]))
    duplicate_every = _duplicate_every(conversations, args.duplicates)
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="memorist-heavy-import-") as temp_dir:
        base = Path(temp_dir)
        db_path = base / "memorist.sqlite"
        object_store = base / "objects"
        fixture = base / "openwebui-heavy.zip"
        generate_openwebui_heavy_fixture(
            fixture,
            conversations=conversations,
            messages_per_conversation=messages,
            branches=branches,
            duplicate_every=duplicate_every,
            malicious_content=args.malicious_content,
        )
        os.environ["MEMORIST_DB_PATH"] = str(db_path)
        os.environ["MEMORIST_OBJECT_STORE_PATH"] = str(object_store)
        get_settings.cache_clear()

        connection = connect(db_path)
        try:
            apply_migrations(connection)
            live_session_uuid = _seed_live_session(connection)
            first = _run_import(connection, fixture, object_store, db_path, args.batch_size)
            duplicate = _run_import(connection, fixture, object_store, db_path, args.batch_size)
            consistency = run_consistency_check(connection)
            live_latency_ms = _capture_during_background_import(
                connection,
                fixture,
                object_store,
                db_path,
                live_session_uuid,
                args.batch_size,
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = _base_result(args.mode)
            result.update(
                {
                    "status": "ok",
                    "passed": True,
                    "skipped": False,
                    "mode": args.mode,
                    "conversations": conversations,
                    "messages_per_conversation": messages,
                    "branches": branches,
                    "duplicates_requested": args.duplicates,
                    "malicious_content": args.malicious_content,
                    "first_import": first,
                    "duplicate_import": duplicate,
                    "live_capture_latency_ms": live_latency_ms,
                    "duration_ms": duration_ms,
                    "consistency": {
                        "status": consistency["status"],
                        "issue_count": consistency["issue_count"],
                    },
                    "writer": get_write_actor(db_path).diagnostics(),
                }
            )
            failed = (
                first["status"] != "committed"
                or first["imported_conversations"] < conversations
                or duplicate["status"] != "committed"
                or duplicate["skipped_records"] < conversations
                or live_latency_ms > 5_000
                or consistency["status"] != "ok"
                or (args.max_seconds is not None and duration_ms > args.max_seconds * 1000)
            )
            if failed:
                result["status"] = "failed"
                result["passed"] = False
                result["failing_step"] = "heavy_import_smoke"
            _write_report(args.report_out, result)
            return result
        finally:
            connection.close()
            get_write_actor(db_path).stop_sync()
            get_settings.cache_clear()


def _run_import(
    connection: Any,
    fixture: Path,
    object_store: Path,
    db_path: Path,
    batch_size: int,
) -> dict[str, Any]:
    service = ImportService(connection, str(object_store))
    run = service.upload(str(fixture), mode="inspect")
    service.inspect(run["import_run_uuid"])
    service.reconstruct(run["import_run_uuid"], "openwebui")
    service.dry_run(run["import_run_uuid"])
    return commit_import_via_actor(
        db_path,
        str(object_store),
        run["import_run_uuid"],
        batch_size=batch_size,
        timeout_per_batch=120,
    )


def _base_result(mode: str) -> dict[str, Any]:
    if mode == "ci-small":
        name = "heavy_import_ci_small"
    else:
        name = f"heavy_import_{mode.replace('-', '_')}"
    return {
        "name": name,
        "passed": False,
        "classification": "real" if mode == "ci-small" else "manual-real",
        "failing_step": None,
        "remediation_hint": None
        if mode == "ci-small"
        else f"Run `python ../release/tests/heavy_import_smoke.py --mode {mode}` locally.",
    }


def _duplicate_every(conversations: int, duplicates: int) -> int:
    if duplicates <= 0:
        return 0
    return max(1, conversations // duplicates)


def _write_report(path: str | None, result: dict[str, Any]) -> None:
    if not path:
        return
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_live_session(connection: Any) -> str:
    workspace = WorkspaceRepository(connection).create_workspace("P2 Heavy Import Smoke")
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        title="Live capture during heavy import",
    )
    MessageRepository(connection).create_message(
        session.session_uuid,
        MessageRole.USER,
        CreatorType.USER,
        raw_text="seed live capture session",
    )
    return session.session_uuid


def _capture_during_background_import(
    connection: Any,
    fixture: Path,
    object_store: Path,
    db_path: Path,
    live_session_uuid: str,
    batch_size: int,
) -> int:
    service = ImportService(connection, str(object_store))
    run = service.upload(str(fixture), mode="inspect")
    service.inspect(run["import_run_uuid"])
    service.reconstruct(run["import_run_uuid"], "openwebui")
    service.dry_run(run["import_run_uuid"])
    error: list[BaseException] = []

    def commit() -> None:
        try:
            commit_import_via_actor(
                db_path,
                str(object_store),
                run["import_run_uuid"],
                batch_size=batch_size,
                timeout_per_batch=120,
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            error.append(exc)

    thread = threading.Thread(target=commit, daemon=True)
    thread.start()
    time.sleep(0.05)
    started = time.perf_counter()
    get_write_actor(db_path).submit_sync(
        CaptureOpenWebUIMessageCommand(
            session_uuid=live_session_uuid,
            role="user",
            content="live message while import is queued",
            idempotency_key="p2-heavy-live-capture",
        ),
        timeout=30,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    thread.join(timeout=180)
    if thread.is_alive():
        raise TimeoutError("background import did not finish")
    if error:
        raise error[0]
    return latency_ms


if __name__ == "__main__":
    raise SystemExit(main())
