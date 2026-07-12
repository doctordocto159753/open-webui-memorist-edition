from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

POSTGRES_MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_.*\.sql$")


def default_postgres_migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def postgres_migration_version(path: Path) -> int:
    match = POSTGRES_MIGRATION_NAME.match(path.name)
    if match is None:
        raise ValueError(f"Invalid PostgreSQL migration filename: {path.name}")
    return int(match.group("version"))


def postgres_migration_files(migrations_dir: Path | None = None) -> list[Path]:
    resolved = default_postgres_migrations_dir() if migrations_dir is None else migrations_dir
    return sorted(resolved.glob("*.sql"), key=postgres_migration_version)


def apply_postgres_migrations(connection: Any, migrations_dir: Path | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                checksum TEXT NOT NULL,
                description TEXT
            )
            """
        )
        cursor.execute("SELECT migration_id, checksum FROM schema_migrations")
        applied = {str(row[0]): str(row[1]) for row in cursor.fetchall()}

        for migration_path in postgres_migration_files(migrations_dir):
            migration_id = migration_path.name
            migration_sql = migration_path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(migration_sql.encode("utf-8")).hexdigest()
            if migration_id in applied:
                if applied[migration_id] != checksum:
                    raise RuntimeError(f"PostgreSQL migration checksum mismatch: {migration_id}")
                continue
            cursor.execute(migration_sql)
            cursor.execute(
                """
                INSERT INTO schema_migrations (migration_id, checksum, description)
                VALUES (%s, %s, %s)
                """,
                (migration_id, checksum, migration_path.stem),
            )
    connection.commit()


def postgres_schema_version(connection: Any) -> int | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'schema_migrations'
            )
            """
        )
        row = cursor.fetchone()
        if row is None or not bool(row[0]):
            return None
        cursor.execute(
            """
            SELECT migration_id
            FROM schema_migrations
            WHERE migration_id ~ '^[0-9]{4}_'
            ORDER BY migration_id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return int(str(row[0])[:4])
