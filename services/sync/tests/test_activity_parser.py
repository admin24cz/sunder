"""Tests for parsing Garmin activity payloads (spec 11.2).

Payload shapes here mirror what `garminconnect` returns, trimmed to the fields
the parser reads. No network is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sunder_sync.domain import ActivityType
from sunder_sync.parsers import (
    ActivityParseError,
    parse_activity_summary,
    parse_activity_type,
    parse_track,
)


def summary(**overrides: Any) -> dict[str, Any]:
    """A realistic Garmin activity summary, overridable per test."""
    payload: dict[str, Any] = {
        "activityId": 12345678901,
        "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-01-15 06:30:00",
        "duration": 2535.4,
        "distance": 10520.3,
        "elevationGain": 126.0,
        "averageHR": 152.4,
        "maxHR": 178.0,
        "averageSpeed": 3.17,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Activity type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_key", "expected"),
    [
        ("running", ActivityType.RUNNING),
        ("trail_running", ActivityType.RUNNING),
        ("treadmill_running", ActivityType.RUNNING),
        ("virtual_run", ActivityType.RUNNING),
        ("cycling", ActivityType.CYCLING),
        ("road_biking", ActivityType.CYCLING),
        ("mountain_biking", ActivityType.CYCLING),
        ("virtual_ride", ActivityType.CYCLING),
        ("gravel_cycling", ActivityType.CYCLING),
        ("lap_swimming", ActivityType.SWIMMING),
        ("open_water_swimming", ActivityType.SWIMMING),
    ],
)
def test_known_garmin_types_map_to_a_sport(type_key: str, expected: ActivityType) -> None:
    """Substring matching so a new Garmin variant lands in the right bucket."""
    assert parse_activity_type(type_key) is expected


def test_type_matching_is_case_insensitive() -> None:
    assert parse_activity_type("Trail_Running") is ActivityType.RUNNING


@pytest.mark.parametrize("type_key", ["strength_training", "yoga", "", None, "kayaking"])
def test_unknown_types_become_other_rather_than_failing(type_key: str | None) -> None:
    """An unrecognised sport is still worth storing (spec 5.2)."""
    assert parse_activity_type(type_key) is ActivityType.OTHER


def test_a_missing_activity_type_container_is_tolerated() -> None:
    parsed = parse_activity_summary(summary(activityType=None))
    assert parsed.activity_type is ActivityType.OTHER


# ---------------------------------------------------------------------------
# Identity fields — strict
# ---------------------------------------------------------------------------


def test_a_full_summary_parses() -> None:
    parsed = parse_activity_summary(summary())

    assert parsed.garmin_activity_id == 12345678901
    assert parsed.activity_type is ActivityType.RUNNING
    assert parsed.started_at == datetime(2026, 1, 15, 6, 30, tzinfo=UTC)
    assert parsed.duration_seconds == 2535
    assert parsed.distance_meters == pytest.approx(10520.3)
    assert parsed.elevation_gain_meters == pytest.approx(126.0)
    assert parsed.avg_heart_rate == 152
    assert parsed.max_heart_rate == 178


@pytest.mark.parametrize("bad_id", [None, "12345", 12.5, True])
def test_a_missing_or_non_integer_activity_id_is_fatal(bad_id: object) -> None:
    """It is the dedup key — guessing would break re-run safety (spec 5.2)."""
    with pytest.raises(ActivityParseError, match="activityId"):
        parse_activity_summary(summary(activityId=bad_id))


def test_a_missing_start_time_is_fatal() -> None:
    payload = summary()
    del payload["startTimeGMT"]
    with pytest.raises(ActivityParseError, match="startTimeGMT|beginTimestamp"):
        parse_activity_summary(payload)


def test_a_malformed_start_time_is_fatal() -> None:
    with pytest.raises(ActivityParseError, match="valid timestamp"):
        parse_activity_summary(summary(startTimeGMT="last tuesday"))


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["2026-01-15 06:30:00", "2026-01-15T06:30:00", "2026-01-15T06:30:00Z", "2026-01-15T06:30:00.0"],
)
def test_the_timestamp_formats_garmin_has_used_all_parse(raw: str) -> None:
    parsed = parse_activity_summary(summary(startTimeGMT=raw))
    assert parsed.started_at == datetime(2026, 1, 15, 6, 30, tzinfo=UTC)


def test_the_result_is_always_timezone_aware() -> None:
    """A naive datetime here would be stored an unknown offset from the truth."""
    assert parse_activity_summary(summary()).started_at.tzinfo is not None


def test_an_epoch_timestamp_is_accepted_as_a_fallback() -> None:
    payload = summary()
    del payload["startTimeGMT"]
    payload["beginTimestamp"] = 1768458600000
    assert parse_activity_summary(payload).started_at == datetime(2026, 1, 15, 6, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Optional metrics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["duration", "distance", "elevationGain", "averageHR", "maxHR", "averageSpeed"],
)
def test_every_metric_is_optional(field: str) -> None:
    payload = summary()
    del payload[field]
    parse_activity_summary(payload)  # must not raise


@pytest.mark.parametrize("field", ["duration", "distance", "averageHR", "maxHR"])
def test_a_null_metric_becomes_none(field: str) -> None:
    parsed = parse_activity_summary(summary(**{field: None}))
    attribute = {
        "duration": "duration_seconds",
        "distance": "distance_meters",
        "averageHR": "avg_heart_rate",
        "maxHR": "max_heart_rate",
    }[field]
    assert getattr(parsed, attribute) is None


def test_a_zero_heart_rate_is_treated_as_not_recorded() -> None:
    """A watch worn without a strap reports 0, not a resting athlete.

    Storing the zero would drag every average that includes it towards nonsense.
    """
    parsed = parse_activity_summary(summary(averageHR=0, maxHR=0))
    assert parsed.avg_heart_rate is None
    assert parsed.max_heart_rate is None


def test_a_zero_elevation_gain_is_kept() -> None:
    """Unlike heart rate, zero climb is a real reading — a flat course."""
    assert parse_activity_summary(summary(elevationGain=0)).elevation_gain_meters == 0.0


def test_heart_rate_is_rounded_to_a_whole_beat() -> None:
    parsed = parse_activity_summary(summary(averageHR=152.6))
    assert parsed.avg_heart_rate == 153


# ---------------------------------------------------------------------------
# Pace
# ---------------------------------------------------------------------------


def test_pace_comes_from_garmins_moving_average_when_present() -> None:
    """`averageSpeed` excludes pauses, which is what an athlete means by pace."""
    parsed = parse_activity_summary(summary(averageSpeed=3.17))
    assert parsed.avg_pace_seconds_per_km == pytest.approx(315.5, rel=0.001)


def test_pace_falls_back_to_distance_over_duration() -> None:
    payload = summary(duration=3000.0, distance=10000.0)
    del payload["averageSpeed"]
    assert parse_activity_summary(payload).avg_pace_seconds_per_km == pytest.approx(300.0)


def test_pace_is_none_when_neither_source_is_available() -> None:
    payload = summary()
    for field in ("averageSpeed", "distance", "duration"):
        del payload[field]
    assert parse_activity_summary(payload).avg_pace_seconds_per_km is None


def test_a_zero_speed_does_not_divide_by_zero() -> None:
    payload = summary(averageSpeed=0)
    del payload["distance"]
    assert parse_activity_summary(payload).avg_pace_seconds_per_km is None


# ---------------------------------------------------------------------------
# Track extraction
# ---------------------------------------------------------------------------


def test_a_track_is_extracted_in_order() -> None:
    details = {
        "geoPolylineDTO": {
            "polyline": [
                {"lat": 50.08, "lon": 14.42, "altitude": 200.0},
                {"lat": 50.081, "lon": 14.421, "altitude": 205.5},
            ]
        }
    }
    track = parse_track(details)

    assert len(track) == 2
    assert track[0].latitude == 50.08
    assert track[0].longitude == 14.42
    assert track[0].altitude_meters == 200.0


def test_a_missing_altitude_is_allowed() -> None:
    """Devices without a barometer omit it; elevation comes from the summary."""
    details = {"geoPolylineDTO": {"polyline": [{"lat": 50.08, "lon": 14.42}]}}
    assert parse_track(details)[0].altitude_meters is None


@pytest.mark.parametrize(
    "details",
    [
        {},
        {"geoPolylineDTO": None},
        {"geoPolylineDTO": {}},
        {"geoPolylineDTO": {"polyline": None}},
        {"geoPolylineDTO": {"polyline": []}},
    ],
)
def test_an_activity_without_gps_yields_an_empty_track(details: dict[str, Any]) -> None:
    """A treadmill run or a pool swim. Normal, not an error."""
    assert parse_track(details) == ()


@pytest.mark.parametrize(
    "bad",
    [
        {"lat": None, "lon": 14.42},
        {"lat": 50.08},
        {"lon": 14.42},
        {"lat": "50.08", "lon": "14.42"},
        {"lat": 91.0, "lon": 14.42},
        {"lat": 50.08, "lon": 181.0},
        "not a dict",
    ],
)
def test_an_unusable_sample_is_skipped_not_fatal(bad: object) -> None:
    """One glitched sample must not cost the whole ride."""
    details = {
        "geoPolylineDTO": {
            "polyline": [
                {"lat": 50.08, "lon": 14.42},
                bad,
                {"lat": 50.081, "lon": 14.421},
            ]
        }
    }
    assert len(parse_track(details)) == 2


def test_has_track_needs_two_points_to_form_a_line() -> None:
    """PostGIS rejects a one-point LineString, and it is not a route anyway."""
    from sunder_sync.models import ParsedActivity, TrackPoint

    base = parse_activity_summary(summary())
    assert not base.has_track

    one = ParsedActivity(
        garmin_activity_id=1,
        activity_type=ActivityType.RUNNING,
        started_at=datetime(2026, 1, 15, tzinfo=UTC),
        track=(TrackPoint(50.08, 14.42),),
    )
    assert not one.has_track

    two = ParsedActivity(
        garmin_activity_id=1,
        activity_type=ActivityType.RUNNING,
        started_at=datetime(2026, 1, 15, tzinfo=UTC),
        track=(TrackPoint(50.08, 14.42), TrackPoint(50.081, 14.421)),
    )
    assert two.has_track
