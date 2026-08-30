from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator


router = APIRouter()


class TrackVesselRequest(BaseModel):
    mmsi: str
    personal_label: str | None = Field(default=None, max_length=100)

    @field_validator("mmsi")
    @classmethod
    def validate_mmsi(cls, value: str) -> str:
        value = value.strip()
        if len(value) != 9 or not value.isdigit():
            raise ValueError("MMSI must contain exactly nine digits")
        return value


def _services(request: Request) -> Any:
    return request.app.state.services


@router.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def ready(request: Request) -> dict[str, Any]:
    services = _services(request)
    try:
        await services.database.stats()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {
        "status": "ready",
        "stream_state": services.ais.status.state,
        "openwaters_state": services.openwaters.status.state,
    }


@router.get("/api/v1/status")
async def status(request: Request) -> dict[str, Any]:
    services = _services(request)
    return {
        "stream": services.ais.status.public_dict(),
        "openwaters": services.openwaters.status.public_dict(),
        "ingestion": services.ingestor.metrics.public_dict(),
        "writer": {
            "queued": services.writer.queue.qsize(),
            "stored_positions_since_start": services.writer.metrics.stored_positions,
            "stored_static_since_start": services.writer.metrics.stored_static,
            "dropped": services.writer.metrics.dropped,
            "errors": services.writer.metrics.errors,
        },
        "browser_clients": services.broadcaster.client_count,
        "database": await services.database.stats(),
        "history_sample_seconds": services.settings.history_sample_seconds,
        "trip_method": f"observation_gap_{services.settings.trip_gap_hours}h_v1",
    }


@router.get("/api/v1/vessels")
async def current_vessels(request: Request) -> dict[str, Any]:
    vessels = await _services(request).database.current_vessels()
    return {"vessels": vessels}


@router.get("/api/v1/tracked-vessels")
async def tracked_vessels(
    request: Request, include_inactive: bool = True
) -> dict[str, Any]:
    rows = await _services(request).database.list_tracked_vessels(include_inactive)
    return {"vessels": rows}


@router.get("/api/v1/tracked-vessels/export")
async def export_tracked_vessels(request: Request) -> dict[str, Any]:
    rows = await _services(request).database.list_tracked_vessels(include_inactive=False)
    return {
        "vessels": [
            {
                "mmsi": row["mmsi"],
                **(
                    {"personal_label": row["personal_label"]}
                    if row.get("personal_label")
                    else {}
                ),
            }
            for row in rows
        ]
    }


@router.post("/api/v1/tracked-vessels", status_code=201)
async def add_tracked_vessel(
    payload: TrackVesselRequest, request: Request
) -> dict[str, Any]:
    services = _services(request)
    try:
        vessel = await services.database.add_tracked_vessel(
            payload.mmsi,
            payload.personal_label,
            services.settings.aisstream_max_mmsis,
        )
    except OverflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    services.ais.request_subscription_refresh()
    services.openwaters.request_subscription_refresh()
    return {"vessel": vessel, "subscription_update": "pending"}


@router.delete("/api/v1/tracked-vessels/{mmsi}", status_code=204)
async def deactivate_tracked_vessel(mmsi: str, request: Request) -> Response:
    if len(mmsi) != 9 or not mmsi.isdigit():
        raise HTTPException(status_code=422, detail="MMSI must contain exactly nine digits")
    services = _services(request)
    changed = await services.database.deactivate_tracked_vessel(mmsi)
    if not changed:
        raise HTTPException(status_code=404, detail="active tracked vessel not found")
    services.ais.request_subscription_refresh()
    services.openwaters.request_subscription_refresh()
    return Response(status_code=204)


@router.get("/api/v1/vessels/{mmsi}/positions")
async def historical_positions(
    mmsi: str,
    request: Request,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    tolerance_m: float = Query(default=25, ge=0, le=10_000),
) -> dict[str, Any]:
    start, end = _time_range(start, end)
    services = _services(request)
    track = await services.database.historical_track(
        mmsi, start, end, tolerance_m, services.settings.track_gap_minutes
    )
    if track is None:
        return {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "mmsi": mmsi,
                "source_point_count": 0,
                "returned_point_count": 0,
                "simplified": False,
                "gap_threshold_minutes": services.settings.track_gap_minutes,
            },
            "segments": [],
        }
    geometry = track.pop("geometry")
    segments = track.pop("segments", [])
    return {"type": "Feature", "geometry": geometry, "properties": track, "segments": segments}


@router.get("/api/v1/vessels/{mmsi}/trips")
async def provisional_trips(
    mmsi: str,
    request: Request,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
) -> dict[str, Any]:
    start, end = _time_range(start, end)
    services = _services(request)
    trips = await services.database.provisional_trips(
        mmsi, start, end, services.settings.trip_gap_hours
    )
    return {
        "trips": trips,
        "provisional": True,
        "explanation": "Segments are separated by long observation gaps; port-based trips come later.",
    }


@router.websocket("/ws/v1/vessels")
async def vessel_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    services = websocket.app.state.services
    queue = services.broadcaster.subscribe()
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "stream": services.ais.status.public_dict(),
                "openwaters": services.openwaters.status.public_dict(),
            }
        )
        disconnect_task = asyncio.create_task(_wait_for_disconnect(websocket))
        while not disconnect_task.done():
            event_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {event_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if disconnect_task in done:
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
                break
            event = event_task.result()
            await websocket.send_json(event)
            queue.task_done()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        if "disconnect_task" in locals():
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task, return_exceptions=True)
        services.broadcaster.unsubscribe(queue)


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


def _time_range(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = _as_utc(end or now)
    start = _as_utc(start or (end - timedelta(days=7)))
    if start >= end:
        raise HTTPException(status_code=422, detail="'from' must be before 'to'")
    if end - start > timedelta(days=3660):
        raise HTTPException(status_code=422, detail="time range cannot exceed ten years")
    return start, end


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
