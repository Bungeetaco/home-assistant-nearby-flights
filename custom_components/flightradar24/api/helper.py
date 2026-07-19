import math
from typing import Any

EARTH_RADIUS_KM = 6371.0
KM_PER_DEG_LAT = 111.32
METERS_PER_FOOT = 0.3048
MPS_TO_KNOTS = 1.943844
MPS_TO_FPM = 196.850394


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

    Same rectangular-box approximation FlightRadar24's own
    get_bounds_by_point uses (not a true circle) - kept consistent so the
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


def to_float(element: Any) -> None | float:
    if element is None:
        return None
    try:
        return float(element)
    except ValueError:
        return None


PHASE_VERTICAL_SPEED_THRESHOLD_FPM = 300.0


def flight_phase(on_ground: bool | None, vertical_speed_fpm: float | None) -> str | None:
    """Coarse phase-of-flight label from data every backend provides
    (on_ground + vertical rate), rather than the schedule/estimated times
    only FlightRadar24 supplied - adsbdb has no live timing data, so the
    OpenSky path can never populate time_scheduled_departure/
    time_estimated_arrival/etc. the way the ticker card's dep/arr display
    used to expect. +-300fpm is comfortably above the noise floor of a
    level cruise (which drifts a little from barometric/wind effects)
    without being so high it misses a real gentle climb or descent.
    """
    if on_ground:
        return "On Ground"
    if vertical_speed_fpm is None:
        return None
    if vertical_speed_fpm >= PHASE_VERTICAL_SPEED_THRESHOLD_FPM:
        return "Departing"
    if vertical_speed_fpm <= -PHASE_VERTICAL_SPEED_THRESHOLD_FPM:
        return "Landing"
    return "Cruising"


def get_value(dictionary: dict, keys: list) -> Any | None:
    nested_dict = dictionary

    for key in keys:
        try:
            nested_dict = nested_dict[key]
        except Exception:
            return None
    return nested_dict
