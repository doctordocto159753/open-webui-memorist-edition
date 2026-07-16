import hashlib
import re
import sqlite3
from pathlib import Path

MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_.*\.sql$")


def migration_version(path: Path) -> int:
    match = MIGRATION_NAME.match(path.name)
    if match is None:
        raise ValueError(f"Invalid migration filename: {path.name}")
    return int(match.group("version"))


def migration_files(migrations_dir: Path) -> list[Path]:
    return sorted(migrations_dir.glob("*.sql"), key=migration_version)


def default_migrations_dir() -> Path:
    source_tree = Path(__file__).resolve().parents[3] / "migrations"
    if source_tree.is_dir():
        return source_tree
    # Wheels relocate memcore into site-packages. The self-contained Docker
    # image intentionally copies the authoritative migration set to
    # /app/migrations and runs with /app as its working directory.
    runtime_tree = Path.cwd() / "migrations"
    if runtime_tree.is_dir():
        return runtime_tree
    raise RuntimeError(
        "SQLite migrations are missing from the runtime package; refusing to start "
        "with an uninitialized canonical store"
    )


def apply_migrations(connection: sqlite3.Connection, migrations_dir: Path | None = None) -> None:
    resolved_migrations_dir = default_migrations_dir() if migrations_dir is None else migrations_dir
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL,
            description TEXT
        )
        """
    )
    applied_migrations = {
        str(row["migration_id"]): str(row["checksum"])
        for row in connection.execute(
            "SELECT migration_id, checksum FROM schema_migrations"
        ).fetchall()
    }

    for migration_path in migration_files(resolved_migrations_dir):
        migration_id = migration_path.name
        migration_sql = migration_path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(migration_sql.encode("utf-8")).hexdigest()
        if migration_id in applied_migrations:
            if applied_migrations[migration_id] != checksum:
                raise RuntimeError(f"Migration checksum mismatch: {migration_id}")
            continue

        connection.executescript(migration_sql)
        connection.execute(
            """
            INSERT INTO schema_migrations (migration_id, applied_at, checksum, description)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?)
            """,
            (migration_id, checksum, migration_path.stem),
        )

    connection.commit()
