from contextlib import AbstractContextManager
from typing import Any, Protocol

from memcore.storage.canonical.capabilities import StoreCapabilities
from memcore.storage.canonical.health import StoreHealth


class CanonicalStore(Protocol):
    @property
    def store_kind(self) -> str: ...

    def health(self) -> StoreHealth: ...

    def capabilities(self) -> StoreCapabilities: ...

    def transaction(self) -> AbstractContextManager[Any]: ...

    def read_connection(self) -> Any: ...

    def write_gateway(self) -> Any: ...
