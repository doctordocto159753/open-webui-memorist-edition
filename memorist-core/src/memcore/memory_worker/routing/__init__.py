"""Memory signal routing from Jakobson sentence annotations."""

from typing import Any

__all__ = ["SignalRouter"]


def __getattr__(name: str) -> Any:
    if name == "SignalRouter":
        from memcore.memory_worker.routing.signal_router import SignalRouter

        return SignalRouter
    raise AttributeError(name)
