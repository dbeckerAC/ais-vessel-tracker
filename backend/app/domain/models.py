from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PositionObservation:
    mmsi: str
    latitude: float
    longitude: float
    received_at: datetime
    source_message_type: str
    source_hash: str
    data_provider: str = "aisstream"
    data_source: str = "aisstream"
    source_station: str | None = None
    speed_over_ground_knots: float | None = None
    course_over_ground_degrees: float | None = None
    true_heading_degrees: int | None = None
    navigational_status: int | None = None
    display_name: str | None = None

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["received_at"] = self.received_at.isoformat().replace("+00:00", "Z")
        return value


@dataclass(slots=True)
class StaticObservation:
    mmsi: str
    received_at: datetime
    source_message_type: str
    payload_hash: str
    normalized_payload: dict[str, Any]


@dataclass(slots=True)
class TrackedVesselInput:
    mmsi: str
    personal_label: str | None = None
