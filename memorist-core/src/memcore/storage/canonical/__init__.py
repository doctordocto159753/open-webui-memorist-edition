"""Canonical store abstraction for Lite and Full runtime profiles."""

from memcore.storage.canonical.base import CanonicalStore
from memcore.storage.canonical.capabilities import StoreCapabilities
from memcore.storage.canonical.factory import assert_startup_ready, create_canonical_store
from memcore.storage.canonical.health import StoreHealth

__all__ = [
    "CanonicalStore",
    "StoreCapabilities",
    "StoreHealth",
    "assert_startup_ready",
    "create_canonical_store",
]
