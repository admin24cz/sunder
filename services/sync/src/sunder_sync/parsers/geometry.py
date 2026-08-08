"""Track simplification (spec section 8.2).

The free tier gives 500 MB of database. A raw GPS track is one sample per second
— an hour's run is ~3,600 points, several hundred kilobytes once stored as
geometry. At that rate a few hundred activities exhaust the whole allowance.

Douglas-Peucker at a 10 m tolerance removes the points that carry no shape: a
straight kilometre collapses to its two endpoints, while a switchback keeps every
turn. Typical reduction is 90–95%, and at 10 m the result is visually identical
on any map zoom a phone will show, and well inside the tolerance segment matching
uses anyway.
"""

from __future__ import annotations

import math

from sunder_sync.models import TrackPoint

DEFAULT_TOLERANCE_METERS = 10.0
"""Spec 8.2. Also comfortably below consumer GPS error, which is 3–5 m at best."""

EARTH_RADIUS_METERS = 6_371_008.8
"""IUGG mean radius."""


def haversine_meters(a: TrackPoint, b: TrackPoint) -> float:
    """Great-circle distance between two points, in metres.

    Haversine rather than a planar approximation because this is also used to
    measure total track length, where the errors of a flat-earth formula would
    accumulate over a whole activity.
    """
    lat1, lon1 = math.radians(a.latitude), math.radians(a.longitude)
    lat2, lon2 = math.radians(b.latitude), math.radians(b.longitude)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, h)))


def _perpendicular_distance_meters(point: TrackPoint, start: TrackPoint, end: TrackPoint) -> float:
    """Distance from `point` to the segment `start`–`end`, in metres.

    Projects onto a local equirectangular plane centred on the segment. Over the
    span of one Douglas-Peucker segment — kilometres at most — the distortion is
    far below the 10 m tolerance, and it avoids the cost and the pole/antimeridian
    edge cases of doing this on a sphere.

    Distance to the *segment*, not to the infinite line: for a track that doubles
    back on itself, the infinite line would report a point as close to a segment
    it is nowhere near, and the switchback would be simplified away.
    """
    # Metres per degree, evaluated at the segment's latitude.
    lat_ref = math.radians((start.latitude + end.latitude) / 2)
    m_per_deg_lat = math.pi * EARTH_RADIUS_METERS / 180
    m_per_deg_lon = m_per_deg_lat * math.cos(lat_ref)

    px = (point.longitude - start.longitude) * m_per_deg_lon
    py = (point.latitude - start.latitude) * m_per_deg_lat
    ex = (end.longitude - start.longitude) * m_per_deg_lon
    ey = (end.latitude - start.latitude) * m_per_deg_lat

    segment_length_squared = ex * ex + ey * ey
    if segment_length_squared == 0.0:
        # Degenerate segment: start and end coincide, so the distance is to the point.
        return math.hypot(px, py)

    # Projection parameter, clamped to the segment rather than the infinite line.
    t = max(0.0, min(1.0, (px * ex + py * ey) / segment_length_squared))
    return math.hypot(px - t * ex, py - t * ey)


def simplify_track(
    points: list[TrackPoint] | tuple[TrackPoint, ...],
    *,
    tolerance_meters: float = DEFAULT_TOLERANCE_METERS,
) -> tuple[TrackPoint, ...]:
    """Reduce a GPS track with Douglas-Peucker, keeping its shape.

    Args:
        points: The track in recorded order.
        tolerance_meters: Maximum distance a removed point may be from the line
            that replaces it. Larger means smaller output and more corner-cutting.

    Returns:
        The simplified track. The first and last points are always kept, so the
        start and finish stay exact. Fewer than three points are returned as-is.

    Raises:
        ValueError: If `tolerance_meters` is not positive. A zero tolerance would
            silently keep every point and quietly defeat the purpose.

    Note:
        Implemented with an explicit stack rather than recursion. A long ride can
        be tens of thousands of points, and the recursive form hits Python's
        recursion limit on exactly the tracks that most need simplifying.
    """
    if tolerance_meters <= 0:
        raise ValueError("tolerance_meters must be positive")

    track = tuple(points)
    if len(track) < 3:
        return track

    keep = [False] * len(track)
    keep[0] = keep[-1] = True

    stack: list[tuple[int, int]] = [(0, len(track) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue

        furthest_index = -1
        furthest_distance = 0.0
        for i in range(first + 1, last):
            distance = _perpendicular_distance_meters(track[i], track[first], track[last])
            if distance > furthest_distance:
                furthest_distance = distance
                furthest_index = i

        # Everything between first and last is within tolerance of the straight
        # line, so the whole span collapses to its endpoints.
        if furthest_distance <= tolerance_meters or furthest_index < 0:
            continue

        keep[furthest_index] = True
        stack.append((first, furthest_index))
        stack.append((furthest_index, last))

    return tuple(point for point, kept in zip(track, keep, strict=True) if kept)


def track_length_meters(points: tuple[TrackPoint, ...]) -> float:
    """Total length along a track, in metres.

    Used for segment distance, where the figure has to come from the geometry
    itself rather than from a Garmin summary field.
    """
    return sum(haversine_meters(a, b) for a, b in zip(points, points[1:], strict=False))


def to_wkt_linestring(points: tuple[TrackPoint, ...]) -> str:
    """Render a track as WKT for PostGIS.

    WKT rather than GeoJSON because PostGREST hands the value to PostGIS as text
    and `geography(LineString, 4326)` parses WKT directly, with no cast needed.

    Note the axis order: WKT is `longitude latitude`, the opposite of how
    coordinates are usually spoken. Getting this backwards puts every Czech
    activity in the Indian Ocean, which is at least an obvious failure.

    Raises:
        ValueError: If there are fewer than two points — PostGIS rejects such a
            LineString, and failing here gives a far clearer message than the
            database error would.
    """
    if len(points) < 2:
        raise ValueError("a LineString needs at least two points")

    coordinates = ", ".join(f"{p.longitude!r} {p.latitude!r}" for p in points)
    return f"LINESTRING({coordinates})"
