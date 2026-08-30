from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.domain.models import PositionObservation
from app.domain.sampling import HistorySampler
from app.persistence.database import Database
from app.services.broadcaster import Broadcaster
from app.services.persistence_writer import PersistenceWriter


@dataclass(slots=True)
class IngestResult:
    accepted: bool
    sampled: bool = False


@dataclass(slots=True)
class IngestionMetrics:
    accepted_positions: int = 0
    stale_positions: int = 0
    sampled_positions: int = 0
    accepted_by_provider: dict[str, int] = field(default_factory=dict)

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


class PositionIngestor:
    def __init__(
        self,
        database: Database,
        writer: PersistenceWriter,
        broadcaster: Broadcaster,
        sample_seconds: int,
    ) -> None:
        self.database = database
        self.writer = writer
        self.broadcaster = broadcaster
        self.sampler = HistorySampler(sample_seconds)
        self.metrics = IngestionMetrics()
        self._latest: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        latest = await self.database.latest_position_times()
        self._latest = dict(latest)
        self.sampler.seed(latest)

    async def submit(self, position: PositionObservation) -> IngestResult:
        async with self._lock:
            previous = self._latest.get(position.mmsi)
            if previous is not None and position.received_at <= previous:
                self.metrics.stale_positions += 1
                return IngestResult(accepted=False)

            self._latest[position.mmsi] = position.received_at
            self.metrics.accepted_positions += 1
            self.metrics.accepted_by_provider[position.data_provider] = (
                self.metrics.accepted_by_provider.get(position.data_provider, 0) + 1
            )
            self.broadcaster.publish(
                {"type": "vessel_update", "vessel": position.public_dict()}
            )

            sampled = self.sampler.should_store(position.mmsi, position.received_at)
            if sampled and self.writer.submit(position):
                self.metrics.sampled_positions += 1
                return IngestResult(accepted=True, sampled=True)
            return IngestResult(accepted=True)
