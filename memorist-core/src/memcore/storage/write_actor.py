from __future__ import annotations

import asyncio
import queue
import sqlite3
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memcore.storage.commands import (
    DEFAULT_MAX_WRITE_WEIGHT,
    BatchPolicy,
    WriteCommand,
    WriteResult,
    command_estimated_write_weight,
    command_max_transaction_ms,
    command_priority,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect, is_sqlite_busy
from memcore.validators.ijson import dump_ijson, load_ijson


@dataclass
class _Envelope:
    command: WriteCommand
    submitted_at: float
    response: queue.Queue[WriteResult | BaseException]
    priority: int
    sequence: int


@dataclass(order=True)
class _QueuedEnvelope:
    sort_priority: int
    sequence: int
    envelope: _Envelope | None = field(compare=False)


class _ActorPriorityQueue(queue.PriorityQueue[_QueuedEnvelope]):
    def __init__(self) -> None:
        super().__init__()
        self._compat_sequence = 0

    def _put(self, item: _QueuedEnvelope | None) -> None:
        if item is None:
            self._compat_sequence += 1
            item = _QueuedEnvelope(9_000, self._compat_sequence, None)
        super()._put(item)


@dataclass
class WriteActorMetrics:
    started: bool = False
    queue_depth: int = 0
    submitted_count: int = 0
    completed_count: int = 0
    error_count: int = 0
    sqlite_busy_count: int = 0
    write_retries: int = 0
    last_command_type: str | None = None
    last_error: str | None = None
    last_fatal_error: str | None = None
    last_queue_wait_ms: int = 0
    last_transaction_ms: int = 0
    max_queue_depth_seen: int = 0
    dead_command_count: int = 0
    queue_depth_by_priority: dict[str, int] = field(default_factory=dict)
    oldest_queued_command_age_ms: int = 0
    p50_queue_wait_ms: int = 0
    p95_queue_wait_ms: int = 0
    p50_transaction_ms: int = 0
    p95_transaction_ms: int = 0
    commands_per_sec: float = 0.0
    import_backpressure_state: str = "idle"

    def as_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "queue_depth": self.queue_depth,
            "submitted_count": self.submitted_count,
            "completed_count": self.completed_count,
            "error_count": self.error_count,
            "sqlite_busy_count": self.sqlite_busy_count,
            "write_retries": self.write_retries,
            "last_command_type": self.last_command_type,
            "last_error": self.last_error,
            "last_fatal_error": self.last_fatal_error,
            "last_queue_wait_ms": self.last_queue_wait_ms,
            "last_transaction_ms": self.last_transaction_ms,
            "max_queue_depth_seen": self.max_queue_depth_seen,
            "dead_command_count": self.dead_command_count,
            "queue_depth_by_priority": self.queue_depth_by_priority,
            "oldest_queued_command_age_ms": self.oldest_queued_command_age_ms,
            "p50_queue_wait_ms": self.p50_queue_wait_ms,
            "p95_queue_wait_ms": self.p95_queue_wait_ms,
            "p50_transaction_ms": self.p50_transaction_ms,
            "p95_transaction_ms": self.p95_transaction_ms,
            "commands_per_sec": self.commands_per_sec,
            "import_backpressure_state": self.import_backpressure_state,
        }


class SQLiteWriteActor:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._queue: _ActorPriorityQueue = _ActorPriorityQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._metrics = WriteActorMetrics()
        self._sequence = 0
        self._queued_priorities: Counter[int] = Counter()
        self._queued_submitted_at: dict[int, float] = {}
        self._wait_samples: deque[int] = deque(maxlen=512)
        self._transaction_samples: deque[int] = deque(maxlen=512)
        self._completion_times: deque[float] = deque(maxlen=1024)

    async def start(self) -> None:
        self.start_sync()

    def start_sync(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="memorist-sqlite-writer",
                daemon=True,
            )
            self._thread.start()
            self._metrics.started = True

    async def stop(self) -> None:
        self.stop_sync()

    def stop_sync(self) -> None:
        self._queue.put(_QueuedEnvelope(10_000, self._next_sequence(), None))
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._metrics.started = False

    async def submit(self, command: WriteCommand, timeout: float | None = None) -> WriteResult:
        return await asyncio.to_thread(self.submit_sync, command, timeout)

    def submit_sync(self, command: WriteCommand, timeout: float | None = None) -> WriteResult:
        self.start_sync()
        response: queue.Queue[WriteResult | BaseException] = queue.Queue(maxsize=1)
        priority = command_priority(command)
        sequence = self._next_sequence()
        envelope = _Envelope(
            command=command,
            submitted_at=time.perf_counter(),
            response=response,
            priority=priority,
            sequence=sequence,
        )
        self._queue.put(_QueuedEnvelope(-priority, sequence, envelope))
        with self._lock:
            self._queued_priorities[priority] += 1
            self._queued_submitted_at[sequence] = envelope.submitted_at
            self._metrics.submitted_count += 1
            self._refresh_queue_metrics_locked()
        item = response.get(timeout=timeout)
        if isinstance(item, BaseException):
            raise item
        return item

    async def submit_batch(
        self,
        commands: list[WriteCommand],
        batch_policy: BatchPolicy,
    ) -> list[WriteResult]:
        return await asyncio.to_thread(self.submit_batch_sync, commands, batch_policy)

    def submit_batch_sync(
        self,
        commands: list[WriteCommand],
        batch_policy: BatchPolicy,
    ) -> list[WriteResult]:
        results: list[WriteResult] = []
        for command in commands[: batch_policy.batch_size]:
            try:
                results.append(self.submit_sync(command))
            except Exception:
                if not batch_policy.continue_on_error:
                    raise
        return results

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_queue_metrics_locked()
            return self._metrics.as_dict()

    def _run(self) -> None:
        connection = connect(self.db_path)
        try:
            apply_migrations(connection)
            while True:
                queued = self._queue.get()
                if queued.envelope is None:
                    return
                envelope = queued.envelope
                with self._lock:
                    self._queued_priorities[envelope.priority] -= 1
                    if self._queued_priorities[envelope.priority] <= 0:
                        del self._queued_priorities[envelope.priority]
                    self._queued_submitted_at.pop(envelope.sequence, None)
                    self._refresh_queue_metrics_locked()
                result_or_error: WriteResult | BaseException
                try:
                    result_or_error = self._execute_envelope(connection, envelope)
                except BaseException as error:
                    result_or_error = error
                    with self._lock:
                        self._metrics.error_count += 1
                        self._metrics.dead_command_count += 1
                        self._metrics.last_error = str(error)[:160]
                        self._metrics.last_fatal_error = (
                            f"{type(error).__name__}: {str(error)[:160]}"
                        )
                envelope.response.put(result_or_error)
                with self._lock:
                    self._refresh_queue_metrics_locked()
        finally:
            connection.close()

    def _execute_envelope(
        self,
        connection: sqlite3.Connection,
        envelope: _Envelope,
    ) -> WriteResult:
        command = envelope.command
        estimated_weight = command_estimated_write_weight(command)
        if estimated_weight > DEFAULT_MAX_WRITE_WEIGHT:
            raise ValueError(
                "Write command exceeds maximum write weight: "
                f"{estimated_weight} > {DEFAULT_MAX_WRITE_WEIGHT}"
            )
        max_transaction_ms = command_max_transaction_ms(command)
        if max_transaction_ms <= 0:
            raise ValueError("Write command has invalid max_transaction_ms")
        wait_ms = int((time.perf_counter() - envelope.submitted_at) * 1000)
        started = time.perf_counter()
        cached = _idempotency_lookup(connection, command)
        if cached is not None:
            replay_validator = getattr(command, "validate_idempotent_replay", None)
            if callable(replay_validator):
                replay_validator(connection)
            result = cached
        else:
            result, busy_retries = self._execute_with_busy_retry(connection, command)
            if busy_retries:
                with self._lock:
                    self._metrics.sqlite_busy_count += busy_retries
                    self._metrics.write_retries += busy_retries
            if command.idempotency_key:
                _record_idempotency(connection, command, result)
        transaction_ms = int((time.perf_counter() - started) * 1000)
        if transaction_ms > max_transaction_ms:
            raise TimeoutError(
                "Write command exceeded bounded transaction budget: "
                f"{transaction_ms}ms > {max_transaction_ms}ms"
            )
        result = WriteResult(
            command_type=result.command_type,
            target_type=result.target_type,
            target_uuid=result.target_uuid,
            result=result.result,
            idempotent_replay=result.idempotent_replay,
            queue_wait_ms=wait_ms,
            transaction_ms=transaction_ms,
        )
        with self._lock:
            self._metrics.completed_count += 1
            self._metrics.last_command_type = command.command_type
            self._metrics.last_queue_wait_ms = wait_ms
            self._metrics.last_transaction_ms = transaction_ms
            self._wait_samples.append(wait_ms)
            self._transaction_samples.append(transaction_ms)
            self._completion_times.append(time.perf_counter())
            self._refresh_sample_metrics_locked()
        return result

    def _execute_with_busy_retry(
        self,
        connection: sqlite3.Connection,
        command: WriteCommand,
        max_attempts: int = 4,
        base_delay_seconds: float = 0.05,
    ) -> tuple[WriteResult, int]:
        busy_retries = 0
        for attempt in range(max_attempts):
            try:
                return command.execute(connection), busy_retries
            except sqlite3.OperationalError as error:
                if not is_sqlite_busy(error) or attempt + 1 >= max_attempts:
                    raise
                busy_retries += 1
                time.sleep(base_delay_seconds * (2**attempt))
        raise RuntimeError("unreachable SQLite busy retry state")

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def _refresh_queue_metrics_locked(self) -> None:
        now = time.perf_counter()
        self._metrics.queue_depth = self._queue.qsize()
        self._metrics.max_queue_depth_seen = max(
            self._metrics.max_queue_depth_seen,
            self._metrics.queue_depth,
        )
        self._metrics.queue_depth_by_priority = {
            str(priority): count for priority, count in sorted(self._queued_priorities.items())
        }
        if self._queued_submitted_at:
            self._metrics.oldest_queued_command_age_ms = int(
                (now - min(self._queued_submitted_at.values())) * 1000
            )
        else:
            self._metrics.oldest_queued_command_age_ms = 0
        if any(priority <= 40 for priority in self._queued_priorities):
            self._metrics.import_backpressure_state = "import_queued"
        elif self._metrics.queue_depth >= 100:
            self._metrics.import_backpressure_state = "queue_pressure"
        else:
            self._metrics.import_backpressure_state = "idle"
        self._refresh_sample_metrics_locked()

    def _refresh_sample_metrics_locked(self) -> None:
        self._metrics.p50_queue_wait_ms = _percentile(list(self._wait_samples), 50)
        self._metrics.p95_queue_wait_ms = _percentile(list(self._wait_samples), 95)
        self._metrics.p50_transaction_ms = _percentile(list(self._transaction_samples), 50)
        self._metrics.p95_transaction_ms = _percentile(list(self._transaction_samples), 95)
        now = time.perf_counter()
        while self._completion_times and now - self._completion_times[0] > 60:
            self._completion_times.popleft()
        self._metrics.commands_per_sec = round(len(self._completion_times) / 60, 3)


_ACTORS: dict[str, SQLiteWriteActor] = {}
_ACTORS_LOCK = threading.Lock()


def get_write_actor(db_path: str | Path) -> SQLiteWriteActor:
    resolved = str(Path(db_path).resolve())
    with _ACTORS_LOCK:
        actor = _ACTORS.get(resolved)
        if actor is None:
            actor = SQLiteWriteActor(resolved)
            _ACTORS[resolved] = actor
        return actor


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    index = int(round((percentile / 100) * (len(sorted_values) - 1)))
    return sorted_values[index]


def _idempotency_lookup(
    connection: sqlite3.Connection,
    command: WriteCommand,
) -> WriteResult | None:
    if not command.idempotency_key:
        return None
    row = connection.execute(
        """
        SELECT *
        FROM write_idempotency_records
        WHERE idempotency_key = ?
          AND command_type = ?
        """,
        (command.idempotency_key, command.command_type),
    ).fetchone()
    if row is None:
        return None
    result = load_ijson(row["result_ijson"])
    if not isinstance(result, dict):
        result = {"value": result}
    if "duplicate" in result:
        result["duplicate"] = True
    return WriteResult(
        command_type=str(row["command_type"]),
        target_type=row["target_type"],
        target_uuid=row["target_uuid"],
        result=result,
        idempotent_replay=True,
    )


def _record_idempotency(
    connection: sqlite3.Connection,
    command: WriteCommand,
    result: WriteResult,
) -> None:
    if not command.idempotency_key:
        return
    with connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO write_idempotency_records (
                idempotency_key, command_type, target_type, target_uuid,
                result_ijson, created_at, schema_version
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
            """,
            (
                command.idempotency_key,
                command.command_type,
                result.target_type,
                result.target_uuid,
                dump_ijson(result.result),
            ),
        )
