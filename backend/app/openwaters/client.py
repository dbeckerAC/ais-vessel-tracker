from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import websockets

from app.config import Settings
from app.domain.models import PositionObservation
from app.domain.normalize import POSITION_MESSAGE_TYPES, normalize_position
from app.persistence.database import Database
from app.services.position_ingestor import PositionIngestor


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenWatersStatus:
    state: str = "starting"
    connected_at: str | None = None
    last_message_at: str | None = None
    last_error: str | None = None
    reconnect_count: int = 0
    received_messages: int = 0
    accepted_positions: int = 0
    stale_positions: int = 0
    subscription_size: int = 0
    omitted_mmsis: int = 0

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpenWatersService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        ingestor: PositionIngestor,
    ) -> None:
        self.settings = settings
        self.database = database
        self.ingestor = ingestor
        self.status = OpenWatersStatus()
        self._stop = asyncio.Event()
        self._refresh = asyncio.Event()
        self._active_mmsis: set[str] = set()

    def request_subscription_refresh(self) -> None:
        self._refresh.set()

    def stop(self) -> None:
        self._stop.set()
        self._refresh.set()

    async def run(self) -> None:
        if not self.settings.openwaters_enabled:
            self.status.state = "disabled"
            return

        backoff = 1.0
        while not self._stop.is_set():
            mmsis = await self._subscription_mmsis()
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
                logger.warning("Open Waters connection lost: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff + random.random())
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

    async def _run_connection(self, initial_mmsis: list[str]) -> None:
        self.status.state = "connecting"
        headers = (
            {"Authorization": f"Bearer {self.settings.openwaters_api_key}"}
            if self.settings.openwaters_api_key
            else None
        )
        async with websockets.connect(
            self.settings.openwaters_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_queue=1024,
        ) as websocket:
            await websocket.send(json.dumps(self._subscription(initial_mmsis)))
            self._active_mmsis = set(initial_mmsis)
            self.status.state = "streaming"
            self.status.connected_at = _utc_now()
            self.status.last_error = None

            updater = asyncio.create_task(self._subscription_updater(websocket))
            try:
                async for raw in websocket:
                    self.status.last_message_at = _utc_now()
                    self.status.received_messages += 1
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    event = json.loads(raw)
                    if event.get("type") == "error":
                        raise RuntimeError(str(event.get("error") or "Open Waters stream error"))
                    await self.handle_event(event)
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
            mmsis = await self._subscription_mmsis()
            if not mmsis:
                await websocket.close(code=1000, reason="no tracked vessels")
                return
            await websocket.send(json.dumps(self._subscription(mmsis)))
            self._active_mmsis = set(mmsis)

    async def _subscription_mmsis(self) -> list[str]:
        active = await self.database.active_mmsis()
        limit = self.settings.openwaters_max_mmsis
        selected = active[:limit]
        self.status.subscription_size = len(selected)
        self.status.omitted_mmsis = max(0, len(active) - len(selected))
        return selected

    @staticmethod
    def _subscription(mmsis: list[str]) -> dict[str, Any]:
        return {"type": "subscribe", "mmsi": [int(mmsi) for mmsi in mmsis]}

    async def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "event":
            return
        mmsi = str(event.get("mmsi") or "").zfill(9)
        if mmsi not in self._active_mmsis:
            return
        position = normalize_openwaters_position(event)
        if position is None:
            return
        result = await self.ingestor.submit(position)
        if result.accepted:
            self.status.accepted_positions += 1
        else:
            self.status.stale_positions += 1

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


def normalize_openwaters_position(event: dict[str, Any]) -> PositionObservation | None:
    message_type = str(event.get("msg_type") or "")
    if message_type not in POSITION_MESSAGE_TYPES:
        return None
    observed_at = _event_time(event.get("time"))
    if observed_at is None:
        return None
    payload = event.get("message")
    if not isinstance(payload, dict):
        return None
    metadata = {
        "MMSI": event.get("mmsi"),
        "ShipName": event.get("name"),
        "Latitude": event.get("lat"),
        "Longitude": event.get("lon"),
    }
    envelope = {
        "MessageType": message_type,
        "MetaData": metadata,
        "Message": {message_type: payload},
    }
    position = normalize_position(envelope, observed_at)
    if position is None:
        return None
    position.data_provider = "openwaters"
    position.data_source = str(event.get("source") or "unknown")
    position.source_station = str(event.get("station") or "") or None
    return position


def _event_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        return None
    return parsed


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
