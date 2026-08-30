from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.domain.models import PositionObservation, StaticObservation


POSITION_MESSAGE_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
}
STATIC_MESSAGE_TYPES = {"ShipStaticData", "StaticDataReport"}


def _lookup(mapping: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value).replace("@", " ")).strip()
    return cleaned or None


def _mmsi(metadata: dict[str, Any], payload: dict[str, Any]) -> str | None:
    value = _lookup(metadata, "MMSI") or _lookup(payload, "UserID", "MMSI")
    if value is None:
        return None
    text = str(value).strip()
    return text.zfill(9) if text.isdigit() and len(text) <= 9 else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _number_in_range(value: Any, minimum: float, maximum: float) -> float | None:
    number = _float_or_none(value)
    return number if number is not None and minimum <= number <= maximum else None


def _integer_in_range(value: Any, minimum: int, maximum: int) -> int | None:
    number = _int_or_none(value)
    return number if number is not None and minimum <= number <= maximum else None


def _hash_payload(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_position(
    envelope: dict[str, Any], received_at: datetime | None = None
) -> PositionObservation | None:
    message_type = str(envelope.get("MessageType", ""))
    if message_type not in POSITION_MESSAGE_TYPES:
        return None

    metadata = envelope.get("MetaData") or {}
    message = envelope.get("Message") or {}
    payload = message.get(message_type) or {}
    if not isinstance(metadata, dict) or not isinstance(payload, dict):
        return None

    if _lookup(payload, "Valid") is False:
        return None

    mmsi = _mmsi(metadata, payload)
    latitude = _float_or_none(_lookup(payload, "Latitude"))
    longitude = _float_or_none(_lookup(payload, "Longitude"))
    if latitude is None:
        latitude = _float_or_none(_lookup(metadata, "Latitude"))
    if longitude is None:
        longitude = _float_or_none(_lookup(metadata, "Longitude"))
    if mmsi is None or latitude is None or longitude is None:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    now = received_at or datetime.now(timezone.utc)
    return PositionObservation(
        mmsi=mmsi,
        latitude=latitude,
        longitude=longitude,
        received_at=now,
        source_message_type=message_type,
        source_hash=_hash_payload({"type": message_type, "metadata": metadata, "payload": payload}),
        speed_over_ground_knots=_number_in_range(_lookup(payload, "Sog"), 0, 102.2),
        course_over_ground_degrees=_number_in_range(_lookup(payload, "Cog"), 0, 359.9),
        true_heading_degrees=_integer_in_range(_lookup(payload, "TrueHeading"), 0, 359),
        navigational_status=_integer_in_range(
            _lookup(payload, "NavigationalStatus"), 0, 15
        ),
        display_name=_clean_text(_lookup(metadata, "ShipName", "Name")),
    )


def normalize_static(
    envelope: dict[str, Any], received_at: datetime | None = None
) -> StaticObservation | None:
    message_type = str(envelope.get("MessageType", ""))
    if message_type not in STATIC_MESSAGE_TYPES:
        return None

    metadata = envelope.get("MetaData") or {}
    message = envelope.get("Message") or {}
    payload = message.get(message_type) or {}
    if not isinstance(metadata, dict) or not isinstance(payload, dict):
        return None
    if _lookup(payload, "Valid") is False:
        return None
    mmsi = _mmsi(metadata, payload)
    if mmsi is None:
        return None

    report_a = payload.get("ReportA")
    if not isinstance(report_a, dict) or _lookup(report_a, "Valid") is False:
        report_a = {}
    report_b = payload.get("ReportB")
    if not isinstance(report_b, dict) or _lookup(report_b, "Valid") is False:
        report_b = {}

    normalized = {
        "display_name": _clean_text(
            _lookup(payload, "Name")
            or _lookup(report_a, "Name")
            or _lookup(metadata, "ShipName", "Name")
        ),
        "call_sign": _clean_text(
            _lookup(payload, "CallSign") or _lookup(report_b, "CallSign")
        ),
        "imo": _int_or_none(_lookup(payload, "ImoNumber", "IMO")),
        "ship_type": _int_or_none(
            _lookup(payload, "Type", "ShipType") or _lookup(report_b, "ShipType")
        ),
        "destination": _clean_text(_lookup(payload, "Destination")),
        "draught": _float_or_none(_lookup(payload, "MaximumStaticDraught", "Draught")),
        "raw": payload,
    }
    return StaticObservation(
        mmsi=mmsi,
        received_at=received_at or datetime.now(timezone.utc),
        source_message_type=message_type,
        payload_hash=_hash_payload(normalized),
        normalized_payload=normalized,
    )
