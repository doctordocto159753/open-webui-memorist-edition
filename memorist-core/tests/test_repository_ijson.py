import sqlite3

import pytest

from memcore.repositories import SQLiteRepository
from memcore.validators.ijson import IJsonValidationError
from memcore.validators.payload_policy import PayloadPolicyError


@pytest.fixture()
def connection() -> sqlite3.Connection:
    sqlite_connection = sqlite3.connect(":memory:")
    sqlite_connection.execute(
        """
        CREATE TABLE repository_events (
            event_uuid TEXT PRIMARY KEY,
            payload_ijson TEXT NOT NULL,
            metadata_ijson TEXT
        )
        """
    )
    return sqlite_connection


def test_repository_insert_with_bad_ijson_fails(connection: sqlite3.Connection) -> None:
    repository = SQLiteRepository(connection)

    with pytest.raises(PayloadPolicyError, match="payload_ijson must contain a top-level"):
        repository.insert(
            "repository_events",
            {
                "event_uuid": "event-1",
                "payload_ijson": '["not", "an", "object"]',
            },
        )


def test_repository_insert_with_invalid_ijson_column_fails(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteRepository(connection)

    with pytest.raises(IJsonValidationError, match="non-finite"):
        repository.insert(
            "repository_events",
            {
                "event_uuid": "event-1",
                "payload_ijson": {"event_type": "job.created", "value": float("inf")},
            },
        )


def test_repository_insert_with_valid_ijson_passes(connection: sqlite3.Connection) -> None:
    repository = SQLiteRepository(connection)

    repository.insert(
        "repository_events",
        {
            "event_uuid": "event-1",
            "payload_ijson": {"event_type": "job.created", "created_at": "2026-06-25T20:00:00Z"},
            "metadata_ijson": {"z": 1, "a": 2},
        },
    )

    row = connection.execute(
        "SELECT payload_ijson, metadata_ijson FROM repository_events WHERE event_uuid = ?",
        ("event-1",),
    ).fetchone()

    assert row == (
        '{"created_at":"2026-06-25T20:00:00Z","event_type":"job.created"}',
        '{"a":2,"z":1}',
    )


def test_repository_update_enforces_ijson_columns(connection: sqlite3.Connection) -> None:
    repository = SQLiteRepository(connection)
    repository.insert(
        "repository_events",
        {
            "event_uuid": "event-1",
            "payload_ijson": {"event_type": "job.created"},
        },
    )

    with pytest.raises(IJsonValidationError, match="outside JavaScript safe range"):
        repository.update(
            "repository_events",
            {"metadata_ijson": {"unsafe": 9_007_199_254_740_992}},
            "event_uuid = ?",
            ("event-1",),
        )
