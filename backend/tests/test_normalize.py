from datetime import datetime, timezone

from app.domain.normalize import normalize_position, normalize_static


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def test_normalizes_position_and_cleans_name():
    envelope = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 211892460, "ShipName": " TEST@@BOAT  "},
        "Message": {
            "PositionReport": {
                "Valid": True,
                "Latitude": 54.1,
                "Longitude": 10.2,
                "Sog": 8.4,
                "Cog": 183.2,
                "TrueHeading": 181,
            }
        },
    }

    result = normalize_position(envelope, NOW)

    assert result is not None
    assert result.mmsi == "211892460"
    assert result.display_name == "TEST BOAT"
    assert result.latitude == 54.1
    assert result.speed_over_ground_knots == 8.4
    assert len(result.source_hash) == 64


def test_rejects_invalid_coordinates():
    envelope = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 211892460},
        "Message": {
            "PositionReport": {"Valid": True, "Latitude": 95, "Longitude": 10}
        },
    }

    assert normalize_position(envelope, NOW) is None


def test_converts_ais_unavailable_navigation_values_to_none():
    envelope = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 211414540},
        "Message": {
            "PositionReport": {
                "Valid": True,
                "Latitude": 52.7,
                "Longitude": 5.1,
                "Sog": 102.3,
                "Cog": 360,
                "TrueHeading": 511,
            }
        },
    }

    result = normalize_position(envelope, NOW)

    assert result is not None
    assert result.speed_over_ground_knots is None
    assert result.course_over_ground_degrees is None
    assert result.true_heading_degrees is None


def test_normalizes_static_data():
    envelope = {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 228238970},
        "Message": {
            "ShipStaticData": {
                "Name": "ALPHA@@@@",
                "CallSign": "FABC",
                "ImoNumber": 9876543,
                "Type": 70,
                "Destination": "HAMBURG@@",
            }
        },
    }

    result = normalize_static(envelope, NOW)

    assert result is not None
    assert result.mmsi == "228238970"
    assert result.normalized_payload["display_name"] == "ALPHA"
    assert result.normalized_payload["destination"] == "HAMBURG"


def test_normalizes_class_b_static_report_parts():
    envelope = {
        "MessageType": "StaticDataReport",
        "MetaData": {"MMSI": 220449000},
        "Message": {
            "StaticDataReport": {
                "Valid": True,
                "ReportA": {"Valid": True, "Name": "SEA BIRD@@@@"},
                "ReportB": {
                    "Valid": True,
                    "CallSign": "OABC",
                    "ShipType": 37,
                },
            }
        },
    }

    result = normalize_static(envelope, NOW)

    assert result is not None
    assert result.normalized_payload["display_name"] == "SEA BIRD"
    assert result.normalized_payload["call_sign"] == "OABC"
    assert result.normalized_payload["ship_type"] == 37
