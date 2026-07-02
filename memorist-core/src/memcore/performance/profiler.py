from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileSample:
    name: str
    latency_ms: float
    cpu_time_ms: float
    peak_rss_bytes: int | None


@contextmanager
def profile_block(name: str) -> Iterator[list[ProfileSample]]:
    started = time.perf_counter()
    cpu_started = time.process_time()
    samples: list[ProfileSample] = []
    try:
        yield samples
    finally:
        samples.append(
            ProfileSample(
                name=name,
                latency_ms=(time.perf_counter() - started) * 1000,
                cpu_time_ms=(time.process_time() - cpu_started) * 1000,
                peak_rss_bytes=None,
            )
        )
