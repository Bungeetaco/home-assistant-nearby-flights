import math
from typing import Any, NamedTuple

EARTH_RADIUS_KM = 6371.0
KM_PER_DEG_LAT = 111.32
METERS_PER_FOOT = 0.3048
MPS_TO_KNOTS = 1.943844
MPS_TO_FPM = 196.850394


class Point(NamedTuple):
    """Trivial lat/lon container.

    Replaces the old backend vendor's client library's own `Entity` class,
    which was used only for this - the last remaining thing that library
    supplied before its client was removed entirely.
    """
    latitude: float
    longitude: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bbox_from_point_radius(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    """Rectangular bounding box (lamin, lomin, lamax, lomax) around a point.

    Same rectangular-box approximation the old backend's own
    get_bounds_by_point used (not a true circle) - kept consistent so the
    existing "distance can exceed the configured radius near a corner, by up
    to radius*sqrt(2)" caveat still applies identically post-switch.
    """
    lat_delta = radius_km / KM_PER_DEG_LAT
    lon_delta = radius_km / (KM_PER_DEG_LAT * max(math.cos(math.radians(lat)), 0.01))
    return (lat - lat_delta, lon - lon_delta, lat + lat_delta, lon + lon_delta)


def meters_to_feet(value: float | None) -> float | None:
    return None if value is None else value / METERS_PER_FOOT


def mps_to_knots(value: float | None) -> float | None:
    return None if value is None else value * MPS_TO_KNOTS


def mps_to_fpm(value: float | None) -> float | None:
    return None if value is None else value * MPS_TO_FPM


def to_int(element: Any) -> None | int:
    if element is None:
        return None
    try:
        return int(element)
    except ValueError:
        return None


PHASE_VERTICAL_SPEED_THRESHOLD_FPM = 300.0

# Below this altitude, a climb/descent is airport-ops phase (departure/arrival) almost
# by definition - matches the real "sterile cockpit"/below-10,000ft aviation convention,
# not an arbitrary number.
PHASE_LOW_ALTITUDE_FT = 10_000.0

# Above PHASE_LOW_ALTITUDE_FT, still treat a climb/descent as Departing/Landing if it's
# within this distance of the resolved origin/destination airport - catches aircraft
# with a high initial climb rate still above 10k ft near the origin, or a long final
# descent that's begun but hasn't dropped below 10k ft yet near the destination.
# Deliberately tighter than typical top-of-descent distance (100-150nm+ for many
# routes) so a flight merely overflying an airport mid-cruise doesn't get mislabeled.
PHASE_NEAR_AIRPORT_KM = 80.0


def flight_phase(
    on_ground: bool | None,
    vertical_speed_fpm: float | None,
    altitude_ft: float | None = None,
    distance_to_origin_km: float | None = None,
    distance_to_destination_km: float | None = None,
) -> str | None:
    """Phase-of-flight label using every signal available for it, not just vertical
    rate. on_ground and vertical_speed_fpm are always present (straight from
    OpenSky); altitude_ft is also always present. distance_to_origin_km/
    distance_to_destination_km are only present when adsbdb resolved a route for
    this callsign - not guaranteed, since adsbdb coverage is incomplete.

    Vertical rate alone can't tell a genuine takeoff/landing apart from a mid-route
    altitude change (an ATC-assigned step climb to a new cruise level, or an early
    descent for weather/traffic/spacing, both common well before top-of-descent) -
    a flight thousands of km from either airport, still climbing after leveling off
    from one step, doesn't belong labeled "Departing". Altitude and proximity to the
    relevant airport are used as corroborating evidence: low altitude (below the
    real aviation ~10,000ft threshold) is airport-ops phase almost by definition;
    above that, being close to the resolved origin/destination is the next-best
    signal. Failing both, a climb/descent this coarse can only honestly be reported
    as Climbing/Descending, not Departing/Landing - adsbdb has no live timing data
    (see the dep/arr comments in flight.py) so there's no schedule to fall back on
    to disambiguate either.

    +-300fpm is comfortably above the noise floor of a level cruise (which drifts a
    little from barometric/wind effects) without being so high it misses a real
    gentle climb or descent.
    """
    if on_ground:
        return "On Ground"
    if vertical_speed_fpm is None:
        return None

    low_altitude = altitude_ft is not None and altitude_ft < PHASE_LOW_ALTITUDE_FT

    if vertical_speed_fpm >= PHASE_VERTICAL_SPEED_THRESHOLD_FPM:
        near_origin = (
            distance_to_origin_km is not None and distance_to_origin_km <= PHASE_NEAR_AIRPORT_KM
        )
        return "Departing" if (low_altitude or near_origin) else "Climbing"

    if vertical_speed_fpm <= -PHASE_VERTICAL_SPEED_THRESHOLD_FPM:
        near_destination = (
            distance_to_destination_km is not None
            and distance_to_destination_km <= PHASE_NEAR_AIRPORT_KM
        )
        return "Landing" if (low_altitude or near_destination) else "Descending"

    return "Cruising"
