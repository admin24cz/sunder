"""Tests for track simplification and geometry helpers (spec 8.2)."""

from __future__ import annotations

import pytest

from sunder_sync.models import TrackPoint
from sunder_sync.parsers import (
    haversine_meters,
    simplify_track,
    to_wkt_linestring,
    track_length_meters,
)

# Prague, roughly. At this latitude 0.00001 degrees of latitude is ~1.1 m, which
# is what makes the tolerances below easy to reason about.
BASE_LAT = 50.08
BASE_LON = 14.42


def point(lat_offset: float = 0.0, lon_offset: float = 0.0) -> TrackPoint:
    return TrackPoint(latitude=BASE_LAT + lat_offset, longitude=BASE_LON + lon_offset)


# ---------------------------------------------------------------------------
# haversine
# ---------------------------------------------------------------------------


def test_distance_between_identical_points_is_zero() -> None:
    assert haversine_meters(point(), point()) == pytest.approx(0.0)


def test_one_degree_of_latitude_is_about_111_km() -> None:
    assert haversine_meters(point(), point(lat_offset=1.0)) == pytest.approx(111_195, rel=0.001)


def test_distance_is_symmetric() -> None:
    a, b = point(), point(lat_offset=0.01, lon_offset=0.02)
    assert haversine_meters(a, b) == pytest.approx(haversine_meters(b, a))


def test_known_distance_prague_to_brno() -> None:
    """~184 km great-circle — a sanity check against a real-world figure."""
    prague = TrackPoint(latitude=50.0755, longitude=14.4378)
    brno = TrackPoint(latitude=49.1951, longitude=16.6068)
    assert haversine_meters(prague, brno) == pytest.approx(184_000, rel=0.02)


# ---------------------------------------------------------------------------
# simplify_track
# ---------------------------------------------------------------------------


def test_short_tracks_pass_through_unchanged() -> None:
    """Nothing can be removed without losing an endpoint."""
    for size in (0, 1, 2):
        track = tuple(point(lat_offset=i * 0.001) for i in range(size))
        assert simplify_track(track) == track


def test_a_straight_line_collapses_to_its_endpoints() -> None:
    """The whole point: a straight kilometre carries no shape worth storing."""
    track = tuple(point(lat_offset=i * 0.0001) for i in range(100))
    simplified = simplify_track(track, tolerance_meters=10.0)

    assert len(simplified) == 2
    assert simplified[0] == track[0]
    assert simplified[-1] == track[-1]


def test_endpoints_are_always_preserved() -> None:
    """Start and finish must stay exact — they anchor the map and the segments."""
    track = tuple(point(lat_offset=i * 0.0005, lon_offset=(i % 7) * 0.0003) for i in range(200))
    simplified = simplify_track(track)

    assert simplified[0] == track[0]
    assert simplified[-1] == track[-1]


def test_a_sharp_corner_is_kept() -> None:
    """A right-angle turn is exactly the shape simplification must not lose."""
    track = (
        point(),
        point(lat_offset=0.005),
        point(lat_offset=0.005, lon_offset=0.005),
    )
    assert simplify_track(track, tolerance_meters=10.0) == track


def test_a_deviation_below_tolerance_is_removed() -> None:
    """~1 m off a straight line, well under the 10 m tolerance."""
    track = (
        point(),
        point(lat_offset=0.0005, lon_offset=0.00001),
        point(lat_offset=0.001),
    )
    assert len(simplify_track(track, tolerance_meters=10.0)) == 2


def test_a_deviation_above_tolerance_is_kept() -> None:
    """~55 m off a straight line, comfortably over the tolerance."""
    track = (
        point(),
        point(lat_offset=0.0005, lon_offset=0.0008),
        point(lat_offset=0.001),
    )
    assert len(simplify_track(track, tolerance_meters=10.0)) == 3


def test_a_switchback_survives() -> None:
    """A track doubling back is why distance is measured to the segment.

    Against the infinite line through the endpoints, the far end of a hairpin
    projects onto a point it is nowhere near, and the whole turn is removed.
    """
    track = (
        point(),
        point(lat_offset=0.01),
        point(lat_offset=0.0001),
    )
    assert len(simplify_track(track, tolerance_meters=10.0)) == 3


def test_output_keeps_the_original_order() -> None:
    track = tuple(point(lat_offset=i * 0.001, lon_offset=(i % 5) * 0.002) for i in range(60))
    simplified = simplify_track(track)

    indices = [track.index(p) for p in simplified]
    assert indices == sorted(indices)


def test_a_looser_tolerance_never_keeps_more_points() -> None:
    track = tuple(point(lat_offset=i * 0.0007, lon_offset=(i % 11) * 0.0004) for i in range(300))

    sizes = [len(simplify_track(track, tolerance_meters=t)) for t in (1.0, 10.0, 50.0, 200.0)]
    assert sizes == sorted(sizes, reverse=True)


def test_a_realistic_track_shrinks_dramatically() -> None:
    """The reason this exists: 500 MB of database has to last (spec 8.2)."""
    # An hour at one sample per second, gently curving, with GPS-scale noise.
    track = tuple(
        point(lat_offset=i * 0.00002, lon_offset=(i * 0.00001) + (i % 3) * 0.0000015)
        for i in range(3600)
    )
    simplified = simplify_track(track, tolerance_meters=10.0)

    assert len(simplified) < len(track) * 0.1, "expected at least a 90% reduction"


def test_a_very_long_track_does_not_hit_the_recursion_limit() -> None:
    """The recursive formulation fails on exactly the tracks that need this most."""
    track = tuple(point(lat_offset=i * 0.000001) for i in range(50_000))
    assert len(simplify_track(track)) == 2


@pytest.mark.parametrize("tolerance", [0.0, -1.0])
def test_a_non_positive_tolerance_is_rejected(tolerance: float) -> None:
    """Zero would keep every point and silently defeat the purpose."""
    with pytest.raises(ValueError, match="tolerance_meters must be positive"):
        simplify_track(
            (point(), point(lat_offset=0.001), point(lat_offset=0.002)), tolerance_meters=tolerance
        )


# ---------------------------------------------------------------------------
# track_length_meters
# ---------------------------------------------------------------------------


def test_length_of_a_track_with_fewer_than_two_points_is_zero() -> None:
    assert track_length_meters(()) == 0.0
    assert track_length_meters((point(),)) == 0.0


def test_length_sums_the_segments() -> None:
    track = (point(), point(lat_offset=0.001), point(lat_offset=0.002))
    single = haversine_meters(track[0], track[1])
    assert track_length_meters(track) == pytest.approx(2 * single, rel=0.001)


# ---------------------------------------------------------------------------
# to_wkt_linestring
# ---------------------------------------------------------------------------


def test_wkt_uses_longitude_latitude_order() -> None:
    """WKT is lon-lat. Reversing it relocates every Czech run to the ocean."""
    wkt = to_wkt_linestring((TrackPoint(50.08, 14.42), TrackPoint(50.09, 14.43)))
    assert wkt.startswith("LINESTRING(")
    assert "14.42 50.08" in wkt
    assert "14.43 50.09" in wkt


def test_wkt_rejects_a_track_too_short_to_be_a_line() -> None:
    """Fails here with a clear message rather than as a PostGIS error later."""
    with pytest.raises(ValueError, match="at least two points"):
        to_wkt_linestring((point(),))


def test_wkt_does_not_round_away_precision() -> None:
    """Truncated coordinates would move a point by metres."""
    wkt = to_wkt_linestring(
        (TrackPoint(50.0812345, 14.4212345), TrackPoint(50.0912345, 14.4312345))
    )
    assert "14.4212345" in wkt
    assert "50.0812345" in wkt
