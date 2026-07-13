from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from memcore.config import Settings
from memcore.imports.runtime import import_connection


@contextmanager
def memory_control_connection(settings: Settings) -> Iterator[Any]:
    """The only canonical connection selector used by memory-control paths."""

    with import_connection(settings) as connection:
        yield connection
