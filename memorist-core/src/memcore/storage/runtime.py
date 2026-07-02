from memcore.config import Settings
from memcore.storage.canonical.factory import assert_startup_ready
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


def initialize_storage(settings: Settings) -> None:
    if settings.canonical_store == "sqlite":
        connection = connect(settings.db_path)
        try:
            apply_migrations(connection)
        finally:
            connection.close()
        return

    assert_startup_ready(settings)
