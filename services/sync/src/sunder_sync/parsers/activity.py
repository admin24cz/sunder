"""Turn Garmin payloads into domain objects.

This is the only module that knows Garmin's field names. Spec section 7.5
expects Garmin to change things underneath us, and confining that knowledge here
means such a change breaks one module with a clear error rather than producing
subtly wrong numbers three layers away.

The parsing is deliberately tolerant about *optional* metrics and strict about
*identifying* ones. A missing heart rate is normal and becomes `None`; a missing
activity id or start time means the payload is not what we think it is, and
guessing would write a corrupt row that dedup could never match again.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sunder_sync.domain import ActivityType
from sunder_sync.models import ParsedActivity, TrackPoint

logger = logging.getLogger(__name__)


class ActivityParseError(ValueError):
    """A Garmin payload could not be understood.

    Carries field names and value types, never whole payloads — a Garmin
    response can contain session tokens (spec 6.4).
    """


# Garmin's `typeKey` is an open vocabulary with dozens of members
# (`trail_running`, `virtual_ride`, `lap_swimming`, ...) and it gains new ones.
# Matching on substrings rather than an exact list means a new variant lands in
# the right bucket instead of falling out as OTHER.
_TYPE_KEYWORDS: tuple[tuple[tuple[str, ...], ActivityType], ...] = (
    (("running", "run"), ActivityType.RUNNING),
    (("cycling", "biking", "bike", "ride"), ActivityType.CYCLING),
    (("swimming", "swim"), ActivityType.SWIMMING),
)


def parse_activity_type(type_key: str | None) -> ActivityType:
    """Map a Garmin `typeKey` onto a supported activity type.

    Unknown values become `OTHER` rather than raising. An unrecognised sport is
    not a reason to fail a whole sync run — the activity is still worth storing,
    and spec 5.2 calls the type list extensible.
    """
    if not type_key:
        return ActivityType.OTHER

    key = type_key.lower()
    for keywords, activity_type in _TYPE_KEYWORDS:
        if any(keyword in key for keyword in keywords):
            return activity_type
    logger.debug("Unrecognised Garmin activity type %r; storing as 'other'", type_key)
    return ActivityType.OTHER


def _parse_timestamp(payload: dict[str, Any]) -> datetime:
    """Extract the activity start as a timezone-aware UTC datetime.

    Garmin has supplied this in three shapes over time, so all three are
    accepted. Local time is deliberately *not* used: it has no offset attached,
    so an activity recorded abroad would be stored an unknown number of hours
    off, and no later correction would be possible.

    Raises:
        ActivityParseError: If no usable start time is present.
    """
    gmt = payload.get("startTimeGMT")
    if isinstance(gmt, str) and gmt:
        # "2026-01-15 06:30:00" and "2026-01-15T06:30:00.0" both appear.
        text = gmt.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ActivityParseError(f"startTimeGMT is not a valid timestamp: {gmt!r}") from exc
        # Naive means UTC here — the field name says so.
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    epoch_ms = payload.get("beginTimestamp")
    if isinstance(epoch_ms, int | float):
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)

    raise ActivityParseError("activity has no startTimeGMT or beginTimestamp")


def _optional_float(payload: dict[str, Any], key: str) -> float | None:
    """Read an optional numeric field, treating nonsense as absent.

    Garmin reports an unrecorded metric as `null`, as `0`, or by omitting it,
    depending on the field and the device. Zero is treated as missing for
    metrics where zero is not a real reading — see `_optional_positive_float`.
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return None if result != result else result  # NaN check


def _optional_positive_float(payload: dict[str, Any], key: str) -> float | None:
    """Read an optional metric where zero means "not recorded".

    A zero average heart rate or a zero average speed is a device that did not
    measure, not an activity performed at rest. Storing the zero would drag every
    average that includes it towards nonsense.
    """
    value = _optional_float(payload, key)
    return value if value is not None and value > 0 else None


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    """Read an optional integer metric, rounding a float and dropping zero."""
    value = _optional_positive_float(payload, key)
    return None if value is None else round(value)


def _average_pace_seconds_per_km(payload: dict[str, Any]) -> float | None:
    """Derive average pace, preferring Garmin's own speed figure.

    `averageSpeed` is Garmin's moving average and excludes pauses, which is what
    an athlete means by pace. The distance ÷ duration fallback includes stopped
    time and so reads slower; it is used only when the speed field is absent.
    """
    speed = _optional_positive_float(payload, "averageSpeed")
    if speed is not None:
        return 1000.0 / speed

    distance = _optional_positive_float(payload, "distance")
    duration = _optional_positive_float(payload, "duration")
    if distance is not None and duration is not None:
        return duration / (distance / 1000.0)

    return None


def parse_activity_summary(payload: dict[str, Any]) -> ParsedActivity:
    """Parse one activity summary from `get_activities`.

    Args:
        payload: A single element of the Garmin activity list.

    Returns:
        The activity, with unrecorded metrics as `None`. No track — summaries
        carry no GPS; use `parse_track` on the detail payload for that.

    Raises:
        ActivityParseError: If the activity id or the start time is missing or
            malformed. Both identify the row, and a wrong value would break the
            dedup key that makes re-running the sync safe (spec 5.2).
    """
    raw_id = payload.get("activityId")
    if not isinstance(raw_id, int) or isinstance(raw_id, bool):
        raise ActivityParseError(
            f"activityId is missing or not an integer (got {type(raw_id).__name__})"
        )

    type_container = payload.get("activityType")
    type_key = type_container.get("typeKey") if isinstance(type_container, dict) else None

    return ParsedActivity(
        garmin_activity_id=raw_id,
        activity_type=parse_activity_type(type_key),
        started_at=_parse_timestamp(payload),
        duration_seconds=_optional_int(payload, "duration"),
        distance_meters=_optional_positive_float(payload, "distance"),
        # Elevation gain legitimately is zero on a flat course, so it is read
        # with the plain reader rather than the positive-only one.
        elevation_gain_meters=_optional_float(payload, "elevationGain"),
        avg_heart_rate=_optional_int(payload, "averageHR"),
        max_heart_rate=_optional_int(payload, "maxHR"),
        avg_pace_seconds_per_km=_average_pace_seconds_per_km(payload),
    )


def parse_track(details: dict[str, Any]) -> tuple[TrackPoint, ...]:
    """Extract the GPS track from a `get_activity_details` payload.

    Args:
        details: The detail payload for one activity.

    Returns:
        The track in recorded order, or an empty tuple for an activity with no
        GPS — a treadmill run or a pool swim. Absence is normal and is not an
        error.

    Note:
        Points with a missing or out-of-range coordinate are skipped rather than
        failing the activity. A single glitched sample is common; losing the
        whole ride over it is not a reasonable trade.
    """
    polyline_container = details.get("geoPolylineDTO")
    if not isinstance(polyline_container, dict):
        return ()

    raw_points = polyline_container.get("polyline")
    if not isinstance(raw_points, list):
        return ()

    points: list[TrackPoint] = []
    skipped = 0
    for raw in raw_points:
        if not isinstance(raw, dict):
            skipped += 1
            continue

        lat = raw.get("lat")
        lon = raw.get("lon")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            skipped += 1
            continue
        if isinstance(lat, bool) or isinstance(lon, bool):
            skipped += 1
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            skipped += 1
            continue

        altitude = raw.get("altitude")
        points.append(
            TrackPoint(
                latitude=float(lat),
                longitude=float(lon),
                altitude_meters=(
                    float(altitude)
                    if isinstance(altitude, int | float) and not isinstance(altitude, bool)
                    else None
                ),
            )
        )

    if skipped:
        logger.info("Skipped %d unusable GPS sample(s) out of %d", skipped, len(raw_points))

    return tuple(points)
