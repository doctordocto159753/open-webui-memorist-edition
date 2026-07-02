from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from memcore.heritage.comparator import compare_databases  # noqa: E402
from memcore.heritage.package import export_heritage, verify_heritage  # noqa: E402
from memcore.retrieval.lexical import rebuild_index  # noqa: E402
from memcore.storage.migrations import apply_migrations  # noqa: E402
from memcore.storage.sqlite import connect  # noqa: E402
from memcore.storage.write_actor import get_write_actor  # noqa: E402
from memcore.storage.write_commands.heritage_commands import (  # noqa: E402
    restore_heritage_via_actor,
)
from release.tests.golden_fixtures import seed_rich_memory_fixture  # noqa: E402


def run() -> dict[str, Any]:
    exit_code = main()
    return {
        "name": "heritage_roundtrip",
        "passed": exit_code == 0,
        "classification": "real",
        "failing_step": None if exit_code == 0 else "heritage_roundtrip",
        "remediation_hint": None if exit_code == 0 else "Run `make heritage-roundtrip`.",
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memorist-heritage-roundtrip-") as temp_dir:
        base = Path(temp_dir)
        source_db = base / "source.sqlite"
        restored_db = base / "restored.sqlite"
        package = base / "heritage.zip"
        tampered_package = base / "heritage-tampered.zip"
        object_store = base / "objects"
        source = connect(source_db)
        try:
            apply_migrations(source)
            seeded = seed_rich_memory_fixture(
                source,
                marker="HERITAGE_GOLDEN_MARKER",
                object_store_path=object_store,
            )
            export_result = export_heritage(source, package)
        finally:
            source.close()
        verification = verify_heritage(package)
        restore_result = restore_heritage_via_actor(package, restored_db, dry_run=False)
        _rebuild_restored_derived_artifacts(restored_db)
        comparison = compare_databases(source_db, restored_db)
        shutil.copy2(package, tampered_package)
        _tamper_package(tampered_package)
        tampered_verification = verify_heritage(tampered_package)
        restored_checks = _restored_content_checks(restored_db, seeded)
        get_write_actor(restored_db).stop_sync()
        result = {
            "status": "ok"
            if verification["valid"]
            and comparison["equivalent"]
            and not tampered_verification["valid"]
            and restored_checks["passed"]
            else "failed",
            "seeded": seeded,
            "export": {
                "record_counts": export_result["manifest"]["record_counts"],
            },
            "verification": verification,
            "tampered_verification": tampered_verification,
            "restore": restore_result,
            "comparison": comparison,
            "restored_checks": restored_checks,
        }
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ok" else 1

def _rebuild_restored_derived_artifacts(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        rebuild_index(connection)
    finally:
        connection.close()


def _restored_content_checks(db_path: Path, seeded: dict[str, Any]) -> dict[str, Any]:
    connection = connect(db_path)
    try:
        checks = {
            "workspace_preserved": _exists(
                connection, "workspaces", "workspace_uuid", seeded["workspace_uuid"]
            ),
            "message_version_preserved": _exists(
                connection,
                "message_versions",
                "message_version_uuid",
                seeded["message_version_uuid"],
            ),
            "memory_preserved": _exists(
                connection, "memories", "memory_uuid", seeded["memory_uuid"]
            ),
            "memory_version_preserved": _exists(
                connection,
                "memory_versions",
                "memory_version_uuid",
                seeded["memory_version_uuid"],
            ),
            "evidence_link_preserved": _exists(
                connection, "memory_evidence_links", "evidence_uuid", seeded["evidence_uuid"]
            ),
            "block_lineage_preserved": _exists(
                connection,
                "memory_block_sources",
                "memory_version_uuid",
                seeded["memory_version_uuid"],
            ),
            "attachment_sources_preserved": _attachment_sources_preserved(connection, seeded),
            "erasure_receipt_preserved": _exists(
                connection,
                "erasure_receipts",
                "erasure_receipt_uuid",
                seeded["erasure_receipt_uuid"],
            ),
            "erased_message_not_resurrected": _erased_message_not_resurrected(
                connection, seeded["tool_message_uuid"]
            ),
            "fts_rebuilt_or_skipped": _fts_status(connection),
            "graph_projection": {
                "status": "skipped_with_reason",
                "reason": "graph_backend_disabled",
            },
            "object_store": {
                "status": "skipped_with_reason",
                "reason": "Heritage object payload export is not implemented in this beta",
                "reference": seeded["object_reference"],
            },
        }
        passed = all(
            bool(value) if isinstance(value, bool) else value["status"] != "failed"
            for value in checks.values()
        )
        return {"passed": passed, "checks": checks}
    finally:
        connection.close()


def _exists(connection: Any, table: str, column: str, value: str) -> bool:
    row = connection.execute(
        f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
        (value,),
    ).fetchone()
    return row is not None


def _attachment_sources_preserved(connection: Any, seeded: dict[str, Any]) -> bool:
    row = connection.execute(
        """
        SELECT source_memory_uuids_ijson, source_block_uuids_ijson
        FROM memory_context_attachments
        WHERE attachment_uuid = ?
        """,
        (seeded["attachment_uuid"],),
    ).fetchone()
    return (
        row is not None
        and seeded["memory_uuid"] in str(row["source_memory_uuids_ijson"])
        and seeded["block_uuid"] in str(row["source_block_uuids_ijson"])
    )


def _erased_message_not_resurrected(connection: Any, message_uuid: str) -> bool:
    row = connection.execute(
        "SELECT raw_text, redaction_status FROM messages WHERE message_uuid = ?",
        (message_uuid,),
    ).fetchone()
    return row is not None and row["raw_text"] is None and row["redaction_status"] == "erased"


def _fts_status(connection: Any) -> dict[str, Any]:
    try:
        count = int(connection.execute("SELECT COUNT(*) FROM memory_version_fts").fetchone()[0])
    except Exception as error:
        return {
            "status": "skipped_with_reason",
            "reason": f"FTS unavailable: {type(error).__name__}",
        }
    return {"status": "rebuilt", "row_count": count}


def _tamper_package(package_path: Path) -> None:
    temp_path = package_path.with_suffix(".tampered.zip")
    with zipfile.ZipFile(package_path, "r") as package:
        manifest = json.loads(package.read("memorist-heritage/manifest.ijson").decode("utf-8"))
        manifest["tampered"] = True
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as tampered:
            for item in package.infolist():
                if item.filename == "memorist-heritage/manifest.ijson":
                    tampered.writestr(
                        item.filename,
                        json.dumps(manifest, separators=(",", ":")),
                    )
                else:
                    tampered.writestr(item, package.read(item.filename))
    temp_path.replace(package_path)


if __name__ == "__main__":
    raise SystemExit(main())
