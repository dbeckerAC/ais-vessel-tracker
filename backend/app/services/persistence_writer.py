from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.domain.models import PositionObservation, StaticObservation
from app.persistence.database import Database


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WriterMetrics:
    queued: int = 0
    stored_positions: int = 0
    stored_static: int = 0
    dropped: int = 0
    errors: int = 0


class PersistenceWriter:
    def __init__(self, database: Database, queue_size: int = 10_000) -> None:
        self.database = database
        self.queue: asyncio.Queue[PositionObservation | StaticObservation] = asyncio.Queue(
            maxsize=queue_size
        )
        self.metrics = WriterMetrics()
        self._stop = asyncio.Event()

    def submit(self, item: PositionObservation | StaticObservation) -> bool:
        try:
            self.queue.put_nowait(item)
            self.metrics.queued = self.queue.qsize()
            return True
        except asyncio.QueueFull:
            self.metrics.dropped += 1
            return False

    async def run(self) -> None:
        pending: list[PositionObservation | StaticObservation] = []
        while not self._stop.is_set() or not self.queue.empty() or pending:
            if not pending:
                try:
                    pending.append(await asyncio.wait_for(self.queue.get(), timeout=0.5))
                except asyncio.TimeoutError:
                    continue

            deadline = asyncio.get_running_loop().time() + 1.0
            while len(pending) < 100 and asyncio.get_running_loop().time() < deadline:
                try:
                    pending.append(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.01)

            positions = [item for item in pending if isinstance(item, PositionObservation)]
            static_items = [item for item in pending if isinstance(item, StaticObservation)]
            try:
                await self.database.persist_positions(positions)
                for item in static_items:
                    await self.database.persist_static(item)
            except Exception:
                self.metrics.errors += 1
                logger.exception("failed to persist AIS batch; retrying")
                await asyncio.sleep(min(30, 2 ** min(self.metrics.errors, 5)))
                continue

            self.metrics.stored_positions += len(positions)
            self.metrics.stored_static += len(static_items)
            for _ in pending:
                self.queue.task_done()
            pending.clear()
            self.metrics.queued = self.queue.qsize()

    def stop(self) -> None:
        self._stop.set()

