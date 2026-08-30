from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import websockets

from app.config import Settings
from app.domain.normalize import normalize_position, normalize_static
from app.persistence.database import Database
from app.services.persistence_writer import PersistenceWriter
from app.services.position_ingestor import PositionIngestor


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamStatus:
    state: str = "starting"
    connected_at: str | None = None
    last_message_at: str | None = None
    last_error: str | None = None
    reconnect_count: int = 0
    received_messages: int = 0
    live_positions: int = 0
    sampled_positions: int = 0
    subscription_size: int = 0
    compression_enabled: bool | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class AisStreamService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        writer: PersistenceWriter,
        ingestor: PositionIngestor,
    ) -> None:
        self.settings = settings
        self.database = database
        self.writer = writer
        self.ingestor = ingestor
        self.status = StreamStatus()
        self._stop = asyncio.Event()
        self._refresh = asyncio.Event()
        self._active_mmsis: set[str] = set()

    def request_subscription_refresh(self) -> None:
        self._refresh.set()

    def stop(self) -> None:
        self._stop.set()
        self._refresh.set()

    async def run(self) -> None:
        if not self.settings.aisstream_api_key:
            self.status.state = "disabled_missing_api_key"
            self.status.last_error = "AISSTREAM_API_KEY is not configured"
            return

        backoff = 1.0
        while not self._stop.is_set():
            mmsis = await self.database.active_mmsis()
            self._active_mmsis = set(mmsis)
            self.status.subscription_size = len(mmsis)
            if not mmsis:
                self.status.state = "waiting_for_tracked_vessel"
                self._refresh.clear()
                await self._wait_for_refresh()
                continue

            try:
                await self._run_connection(mmsis)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status.state = "disconnected"
                self.status.last_error = f"{type(exc).__name__}: {exc}"
                self.status.reconnect_count += 1
                logger.warning("AISStream connection lost: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff + random.random())
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

    async def _run_connection(self, initial_mmsis: list[str]) -> None:
        self.status.state = "connecting"
        async with websockets.connect(
            self.settings.aisstream_url,
            compression="deflate",
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_queue=1024,
        ) as websocket:
            await websocket.send(json.dumps(self._subscription(initial_mmsis)))
            self._active_mmsis = set(initial_mmsis)
            self.status.state = "connected_awaiting_confirmation"
            self.status.connected_at = _utc_now()
            self.status.last_error = None

            updater = asyncio.create_task(self._subscription_updater(websocket))
            try:
                async for raw in websocket:
                    self.status.last_message_at = _utc_now()
                    self.status.received_messages += 1
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    envelope = json.loads(raw)
                    await self.handle_envelope(envelope)
            finally:
                updater.cancel()
                await asyncio.gather(updater, return_exceptions=True)

    async def _subscription_updater(self, websocket: Any) -> None:
        while not self._stop.is_set():
            await self._refresh.wait()
            self._refresh.clear()
            await asyncio.sleep(1.0)
            while self._refresh.is_set():
                self._refresh.clear()
                await asyncio.sleep(1.0)
            mmsis = await self.database.active_mmsis()
            if not mmsis:
                await websocket.close(code=1000, reason="no tracked vessels")
                return
            await websocket.send(json.dumps(self._subscription(mmsis)))
            self._active_mmsis = set(mmsis)
            self.status.subscription_size = len(mmsis)
            self.status.state = "connected_awaiting_confirmation"

    def _subscription(self, mmsis: list[str]) -> dict[str, Any]:
        return {
            "APIKey": self.settings.aisstream_api_key,
            "BoundingBoxes": [[[-90.0, -180.0], [90.0, 180.0]]],
            "FiltersShipMMSI": mmsis,
            "FilterMessageTypes": [
                "PositionReport",
                "StandardClassBPositionReport",
                "ExtendedClassBPositionReport",
                "ShipStaticData",
                "StaticDataReport",
            ],
        }

    async def handle_envelope(self, envelope: dict[str, Any]) -> None:
        if envelope.get("MessageType") == "SubscriptionConfirmation":
            message = envelope.get("Message") or {}
            self.status.compression_enabled = message.get("CompressionEnabled")
            self.status.state = "streaming"
            return

        received_at = datetime.now(timezone.utc)
        position = normalize_position(envelope, received_at)
        if position is not None:
            if position.mmsi not in self._active_mmsis:
                return
            result = await self.ingestor.submit(position)
            if result.accepted:
                self.status.live_positions += 1
            if result.sampled:
                self.status.sampled_positions += 1
            return

        static = normalize_static(envelope, received_at)
        if static is not None and static.mmsi in self._active_mmsis:
            self.writer.submit(static)

    async def _wait_for_refresh(self) -> None:
        refresh = asyncio.create_task(self._refresh.wait())
        stopped = asyncio.create_task(self._stop.wait())
        done, pending = await asyncio.wait(
            {refresh, stopped}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if refresh in done:
            self._refresh.clear()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
