from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from memcore.governance.privacy import PrivacyService  # noqa: E402
from memcore.heritage.package import export_heritage  # noqa: E402
from memcore.privacy.residue_check import run_forget_residue_check  # noqa: E402
from memcore.storage.migrations import apply_migrations  # noqa: E402
from memcore.storage.sqlite import connect  # noqa: E402
from memcore.storage.write_actor import get_write_actor  # noqa: E402
from memcore.storage.write_commands.privacy_commands import (  # noqa: E402
    run_privacy_request_command,
)
from release.tests.golden_fixtures import seed_rich_memory_fixture  # noqa: E402

FORBIDDEN_TEXT = "P2_UNIQUE_FORGET_SECRET_DO_NOT_RETAIN"


def run() -> dict[str, Any]:
    exit_code = main()
    return {
        "name": "forget_residue",
        "passed": exit_code == 0,
        "classification": "real",
        "failing_step": None if exit_code == 0 else "forget_residue",
        "remediation_hint": None if exit_code == 0 else "Run `make forget-residue`.",
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memorist-forget-residue-") as temp_dir:
        base = Path(temp_dir)
        db_path = base / "memorist.sqlite"
        connection = connect(db_path)
        try:
            apply_migrations(connection)
            seeded = seed_rich_memory_fixture(
                connection,
                marker=FORBIDDEN_TEXT,
                object_store_path=base / "objects",
                include_receipt=False,
            )
            _scrub_root_transcript_layers(connection, seeded["session_uuid"])
            preview = PrivacyService(connection).preview_request(
                request_type="forget",
                target_type="memory",
                target_uuid=seeded["memory_uuid"],
                requested_scope={"reason": "polish multi-layer residue smoke"},
                actor_type="user",
            )
            run_privacy_request_command(
                db_path,
                preview["privacy_request_uuid"],
                "confirm",
                confirmation_value=preview["confirmation_token"],
            )
            receipt = run_privacy_request_command(
                db_path,
                preview["privacy_request_uuid"],
                "execute",
            )
            residue = run_forget_residue_check(
                connection,
                seeded["memory_uuid"],
                FORBIDDEN_TEXT,
                check_uuid_references=False,
            )
            receipt_has_raw_content = FORBIDDEN_TEXT in json.dumps(receipt, sort_keys=True)
            heritage_path = base / "after-forget-heritage.zip"
            export_heritage(connection, heritage_path)
            heritage_has_raw_content = FORBIDDEN_TEXT.encode("utf-8") in heritage_path.read_bytes()
            result = {
                "status": "ok"
                if (
                    residue["status"] == "clean"
                    and not receipt_has_raw_content
                    and not heritage_has_raw_content
                )
                else "failed",
                "privacy_request_uuid": preview["privacy_request_uuid"],
                "target_memory_uuid": seeded["memory_uuid"],
                "receipt": receipt,
                "receipt_has_raw_content": receipt_has_raw_content,
                "heritage_export_has_raw_content": heritage_has_raw_content,
                "residue": residue,
            }
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "ok" else 1
        finally:
            connection.close()
            get_write_actor(db_path).stop_sync()


def _scrub_root_transcript_layers(connection: Any, session_uuid: str) -> None:
    with connection:
        connection.execute(
            """
            UPDATE messages
            SET raw_text = 'Root transcript retained without forgotten memory content.',
                raw_payload_ijson = NULL,
                snapshot_ijson = NULL
            WHERE session_uuid = ?
            """,
            (session_uuid,),
        )
        connection.execute(
            """
            UPDATE message_versions
            SET raw_text = 'Versioned transcript retained without forgotten memory content.',
                raw_payload_ijson = NULL,
                snapshot_ijson = NULL
            WHERE message_uuid IN (
                SELECT message_uuid FROM messages WHERE session_uuid = ?
            )
            """,
            (session_uuid,),
        )
        connection.execute(
            """
            UPDATE memory_processing_runs
            SET raw_output_ijson = NULL,
                validation_errors_ijson = NULL,
                error_text = NULL
            WHERE session_uuid = ?
            """,
            (session_uuid,),
        )


if __name__ == "__main__":
    raise SystemExit(main())
