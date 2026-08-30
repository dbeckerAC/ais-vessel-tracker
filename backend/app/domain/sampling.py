from __future__ import annotations

from datetime import datetime, timedelta


class HistorySampler:
    def __init__(self, interval_seconds: int) -> None:
        self.interval = timedelta(seconds=interval_seconds)
        self._last_stored: dict[str, datetime] = {}

    def seed(self, latest_by_mmsi: dict[str, datetime]) -> None:
        self._last_stored.update(latest_by_mmsi)

    def should_store(self, mmsi: str, received_at: datetime) -> bool:
        previous = self._last_stored.get(mmsi)
        if previous is not None and received_at - previous < self.interval:
            return False
        self._last_stored[mmsi] = received_at
        return True

