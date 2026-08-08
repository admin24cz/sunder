"""Domain objects the sync service passes between its layers.

These sit between the Garmin payloads and the database rows. Having an explicit
middle representation is what lets the parser tests assert on meaning rather than
on JSON, and what stops a Garmin field-name change from propagating into the
database layer.

Every type here is frozen. A parsed activity is a fact about something that
already happened; nothing downstream has any business editing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sunder_sync.domain import ActivityType, ConnectionStatus


@dataclass(frozen=True, slots=True)
class TrackPoint:
    """One GPS sample.

    Altitude is optional because Garmin omits it for devices without a
    barometer, and elevation is separately reported in the summary anyway.
    """

    latitude: float
    longitude: float
    altitude_meters: float | None = None


@dataclass(frozen=True, slots=True)
class GarminConnection:
    """A user's Garmin link, as read from the database.

    Deliberately carries the password as raw ciphertext rather than a decrypted
    `Secret`. Decryption happens at the moment of login and nowhere else, so a
    connection object can be logged, held in a list, or included in an error
    report without any risk of exposing a credential.
    """

    user_id: str
    garmin_email: str
    encrypted_password: bytes
    status: ConnectionStatus
    last_sync_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ParsedActivity:
    """One activity, normalised out of a Garmin payload.

    Nearly every metric is optional, and that is not defensiveness — a treadmill
    run has no GPS track, a watch worn without a strap records no heart rate, and
    a swim has no meaningful running pace. Modelling them as optional keeps the
    absence explicit instead of encoding it as a zero that later averages into
    the statistics.
    """

    garmin_activity_id: int
    activity_type: ActivityType
    started_at: datetime
    duration_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    avg_pace_seconds_per_km: float | None = None
    track: tuple[TrackPoint, ...] = ()

    @property
    def has_track(self) -> bool:
        """Whether there are enough points to form a line.

        Two, because PostGIS rejects a `LineString` with fewer, and a single
        point is not a route anyone can look at on a map.
        """
        return len(self.track) >= 2


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What happened for one user during one run (spec 7.1, 7.4).

    Returned rather than raised, even on failure. Per-user isolation means the
    runner has to keep going after a failure, so a failure needs to be a value it
    can collect — not control flow that ends the loop.
    """

    user_id: str
    status: ConnectionStatus
    activities_imported: int = 0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether this user's sync completed without an error."""
        return self.error is None
