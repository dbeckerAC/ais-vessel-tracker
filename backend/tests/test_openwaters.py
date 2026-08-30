import asyncio
from datetime import datetime, timedelta, timezone

from app.domain.models import PositionObservation
from app.openwaters.client import OpenWatersService, normalize_openwaters_position
from app.services.position_ingestor import IngestResult, PositionIngestor


NOW = datetime(2026, 8, 27, 17, 30, tzinfo=timezone.utc)


def test_normalizes_openwaters_position_and_provenance():
    event = {
        "type": "event",
        "time": "2026-08-27T17:28:21Z",
        "source": "aishub",
        "station": "aishub",
        "mmsi": 211414540,
        "msg_type": "PositionReport",
        "lat": 52.5526683333,
        "lon": 5.4622533333,
        "message": {
            "Valid": True,
            "UserID": 211414540,
            "Sog": 0,
            "Cog": 320.9,
            "TrueHeading": 511,
        },
    }

    result = normalize_openwaters_position(event)

    assert result is not None
    assert result.mmsi == "211414540"
    assert result.received_at == datetime(2026, 8, 27, 17, 28, 21, tzinfo=timezone.utc)
    assert result.latitude == 52.5526683333
    assert result.data_provider == "openwaters"
    assert result.data_source == "aishub"
    assert result.source_station == "aishub"
    assert result.true_heading_degrees is None


def test_shared_ingestor_rejects_an_older_provider_position():
    class FakeDatabase:
        async def latest_position_times(self):
            return {}

    class FakeWriter:
        def __init__(self):
            self.items = []

        def submit(self, item):
            self.items.append(item)
            return True

    class FakeBroadcaster:
        def __init__(self):
            self.events = []

        def publish(self, event):
            self.events.append(event)

    async def scenario():
        writer = FakeWriter()
        broadcaster = FakeBroadcaster()
        ingestor = PositionIngestor(FakeDatabase(), writer, broadcaster, 60)
        await ingestor.initialize()
        newer = _position(NOW, "openwaters", "aishub")
        older = _position(NOW - timedelta(seconds=5), "aisstream", "aisstream")

        first = await ingestor.submit(newer)
        second = await ingestor.submit(older)

        assert first.accepted and first.sampled
        assert not second.accepted
        assert len(writer.items) == 1
        assert len(broadcaster.events) == 1
        assert ingestor.metrics.stale_positions == 1

    asyncio.run(scenario())


def test_aisstream_relay_from_openwaters_is_submitted():
    event = {
        "type": "event",
        "time": "2026-08-27T17:28:21Z",
        "source": "aisstream",
        "mmsi": 211414540,
        "msg_type": "PositionReport",
        "lat": 52.5526683333,
        "lon": 5.4622533333,
        "message": {
            "Valid": True,
            "UserID": 211414540,
            "Sog": 4.2,
            "Cog": 320.9,
        },
    }

    class FakeIngestor:
        def __init__(self):
            self.positions = []

        async def submit(self, position):
            self.positions.append(position)
            return IngestResult(accepted=True)

    async def scenario():
        ingestor = FakeIngestor()
        service = OpenWatersService(None, None, ingestor)
        service._active_mmsis = {"211414540"}

        await service.handle_event(event)

        assert len(ingestor.positions) == 1
        assert ingestor.positions[0].data_provider == "openwaters"
        assert ingestor.positions[0].data_source == "aisstream"
        assert service.status.accepted_positions == 1

    asyncio.run(scenario())


def _position(at: datetime, provider: str, source: str) -> PositionObservation:
    return PositionObservation(
        mmsi="211414540",
        latitude=52.55,
        longitude=5.46,
        received_at=at,
        source_message_type="PositionReport",
        source_hash="a" * 64,
        data_provider=provider,
        data_source=source,
    )
