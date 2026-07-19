import logging
import re
import time
from typing import Any
from .helper import (
    to_int, haversine_km, meters_to_feet, mps_to_knots, mps_to_fpm, flight_phase, Point,
)
from .event import EventManager
from .opensky import OpenSkyClient
from .adsbdb import AdsbdbClient

_LOGGER = logging.getLogger(__name__)

# FlightRadar24's area/"zones" listing endpoint used to have its own soft-block behavior:
# instead of an HTTP error, a blocked request got a valid 200 response with an empty
# flight list, indistinguishable from a genuine "no traffic in the area right now"
# response. The OpenSky backend below can fail the same way in spirit (a request error,
# or a rate-limited/empty response), so this grace window is kept as generic protection:
# keep serving the last known-good area list for up to this many seconds since it was
# last genuinely non-empty (flagged via self.area_stale so sensor.py/the dashboard card
# can show it's serving cached data); past this window, trust an empty result as real --
# otherwise a genuine, prolonged lull would show stale flights forever.
AREA_STALE_GRACE_S = 600.0
from ..const import (
    EVENT_ENTRY,
    EVENT_EXIT,
    EVENT_AREA_LANDED,
    EVENT_AREA_TOOK_OFF,
)


def is_helicopter(flight) -> bool:
    """Check if a flight is a helicopter based on callsign, model or ICAO code."""

    def get_val(key):
        # `flight.get(key, "")` only falls back to "" when the key is absent, not
        # when it's present with an explicit None value (e.g. adsbdb had no
        # aircraft-type match for this icao24) - `or ""` is needed on both
        # branches so a None value coerces to "" instead of str(None) -> "None",
        # which would otherwise get fed into the regexes below.
        return str(
            (flight.get(key, "") or "")
            if isinstance(flight, dict)
            else (getattr(flight, key, "") or "")
        )

    callsign = get_val("callsign")
    model = get_val("aircraft_model")
    code = get_val("aircraft_code")

    if re.match(
        (
            r"^(LIFELN|POLICE|MEDIC|LL|HELI|SAR|SGR|ZULU|SLAYR|CRNGE|"
            r"VORTX|SHARK|REAPER|APACHE|FIRE|RESCUE|PNTHR|VICTR|CHX|"
            r"NHC|UKP|NPAS|AAC|AMBUSH|BARON|ARCTIC|COAST|KUST|RAINBOW|"
            r"SAMU|DRAG|PEGASO|HEMS)"
        ),
        callsign,
        re.IGNORECASE,
    ):
        return True

    if re.search(
        (
            r"(HELICOPTER|EUROCOPTER|ROBINSON|AGUSTA|BELL\s|SIKORSKY|"
            r"AEROSPATIALE|MD\sHELICOPTERS|GUIMBAL|KAMOV|LEONARDO|"
            r"WESTLAND|APACHE|CHINOOK|GAZELLE|MERLIN|WILDCAT|LYNX|"
            r"PUMA|BOEING\sAH|AH\-64)"
        ),
        model,
        re.IGNORECASE,
    ):
        return True

    if re.match(
        (
            r"^(R22|R44|R66|EC|AS[35]|H1[23467]|H6[045]|H47|AW|"
            r"B[0245]|UH|CH|A1[0-9]|H500|MI[0-9]|NH90|SK[0-9]|"
            r"EH10|LYNX|G2CA|S76|S92|EC45)"
        ),
        code,
        re.IGNORECASE,
    ):
        return True

    return False


class FlightProcessor:
    __slots__ = ('_in_area', '_entered', '_exited', '_min_altitude', '_max_altitude',
                 '_point', '_event_manager',
                 '_last_nonempty_monotonic', '_area_stale',
                 '_opensky', '_adsbdb', '_opensky_bbox')

    def __init__(
            self,
            event_manager: EventManager,
            min_altitude: int,
            max_altitude: int,
            point: Point,
            opensky_client: OpenSkyClient,
            adsbdb_client: AdsbdbClient,
            opensky_bbox: tuple[float, float, float, float],
    ) -> None:
        self._min_altitude = min_altitude
        self._max_altitude = max_altitude
        self._point = point
        self._event_manager = event_manager
        self._opensky = opensky_client
        self._adsbdb = adsbdb_client
        self._opensky_bbox = opensky_bbox
        self._in_area: dict[str, dict[str, Any]] | None = None
        self._entered: list[dict[str, Any]] = []
        self._exited: list[dict[str, Any]] = []
        self._last_nonempty_monotonic: float | None = None
        self._area_stale: bool = False

    @property
    def in_area_list(self) -> list[dict[str, Any]]:
        return list(self._in_area.values()) if self._in_area else []

    @property
    def area_stale(self) -> bool:
        # True while update_flights_in_area() is serving a cached area list because the
        # most recent fetch came back empty and is suspected to be a soft block rather
        # than genuine zero traffic -- see AREA_STALE_GRACE_S above.
        return self._area_stale

    @property
    def entered_list(self) -> list[dict[str, Any]]:
        return self._entered

    @property
    def exited_list(self) -> list[dict[str, Any]]:
        return self._exited

    def clear_live_data(self) -> None:
        self._in_area = {}
        self._entered = []
        self._exited = []

    def set_in_area(self, in_area: dict[str, dict[str, Any]]) -> None:
        # Called only from sensor.py's FlightRadar24RestoreSensor at HA startup, to
        # close a DIFFERENT gap than AREA_STALE_GRACE_S normally covers.
        # AREA_STALE_GRACE_S protects an already-warm self._in_area from a transient
        # soft block; a cold start has nothing to protect at all (self._in_area starts
        # empty, self._last_nonempty_monotonic starts None), so the very first
        # post-restart poll -- if it happens to be soft-blocked -- wipes straight to
        # empty with zero grace window. Confirmed live: after an HA restart,
        # sensor.flightradar24_current_in_area came back empty with stale=false and
        # STAYED that way for several minutes with a real flight independently confirmed
        # present. Restoring the last known flight list from the entity's recorded state
        # AND stamping _last_nonempty_monotonic as "now" gives that first post-restart
        # poll the same grace-window protection a warm cycle would have had.
        self._in_area = in_area
        self._last_nonempty_monotonic = time.monotonic()
        self._area_stale = False

    def update_flights_in_area(self) -> None:
        self._entered = {}
        self._exited = {}

        current = self._update_flights_in_area_opensky()

        if current is None:
            # Suspected soft block / transient failure - the grace-window branch
            # inside _update_flights_in_area_opensky() already logged it and left
            # self._in_area untouched. Skip entered/exited bookkeeping entirely,
            # same reasoning as before: an empty result we don't trust shouldn't
            # report every previously-seen flight as having "exited".
            return

        if self._in_area is not None:
            entries = current.keys() - self._in_area.keys()
            self._entered = [current[x] for x in entries]
            exits = self._in_area.keys() - current.keys()
            self._exited = [self._in_area[x] for x in exits]
            self._event_manager.add_events(EVENT_ENTRY, self._entered)
            self._event_manager.add_events(EVENT_EXIT, self._exited)
        self._in_area = current

    def _update_flights_in_area_opensky(self) -> dict[str, dict[str, Any]] | None:
        """OpenSky (positions) + adsbdb (route/aircraft enrichment) backend for
        the in-area feed. Returns None to mean "treat this cycle as a
        suspected transient failure, keep serving cached data".
        """
        lamin, lomin, lamax, lomax = self._opensky_bbox
        states = None
        try:
            states = self._opensky.get_states_bbox(lamin, lomin, lamax, lomax)
        except Exception as e:
            _LOGGER.warning("OpenSky states/all request failed: %s", e)

        if not states and self._in_area and self._last_nonempty_monotonic is not None and (
            time.monotonic() - self._last_nonempty_monotonic < AREA_STALE_GRACE_S
        ):
            if not self._area_stale:
                _LOGGER.warning(
                    "OpenSky area feed returned empty/failed; suspected transient "
                    "issue, serving cached in-area flight list for up to %.0fs "
                    "since last real data", AREA_STALE_GRACE_S,
                )
            self._area_stale = True
            return None

        if self._area_stale and states:
            _LOGGER.info("OpenSky area feed recovered after suspected transient issue")
        self._area_stale = False

        current: dict[str, dict[str, Any]] = {}
        if states:
            self._last_nonempty_monotonic = time.monotonic()
            for state in states:
                altitude_ft = meters_to_feet(state["altitude_m"])
                if altitude_ft is None or not self._min_altitude <= altitude_ft <= self._max_altitude:
                    continue
                try:
                    flight = self._build_flight_from_opensky(state, altitude_ft)
                except Exception:
                    # One flight's adsbdb enrichment call misbehaving (unexpected
                    # response shape, etc.) must not discard every other flight
                    # already built earlier in this same loop. adsbdb.py already
                    # degrades its own failures to None internally, but this is a
                    # deliberate second layer of protection against any other
                    # per-flight failure here, not just adsbdb's.
                    _LOGGER.warning("%s: skipping flight (build failed)", state.get("icao24"), exc_info=True)
                    continue
                if flight is not None:
                    current[flight["id"]] = flight
        else:
            self._last_nonempty_monotonic = None

        return current

    def _build_flight_from_opensky(self, state: dict[str, Any], altitude_ft: float) -> dict[str, Any] | None:
        icao24 = state["icao24"]
        callsign = state["callsign"] or None
        previous = self._in_area.get(icao24) if self._in_area else None
        previous_closest_distance = previous.get('closest_distance') if previous is not None else None
        last_position = previous.get('on_ground') if previous is not None else None

        route = self._adsbdb.lookup_callsign(callsign) if self._adsbdb and callsign else None
        aircraft = (self._adsbdb.lookup_aircraft(icao24) if self._adsbdb else None) or {}
        origin = (route or {}).get('origin') or {}
        destination = (route or {}).get('destination') or {}
        airline = (route or {}).get('airline') or {}

        new_distance = haversine_km(
            self._point.latitude, self._point.longitude, state["latitude"], state["longitude"]
        )

        flight: dict[str, Any] = {
            'id': icao24,
            'flight_number': None,
            'callsign': callsign,
            'aircraft_registration': aircraft.get('registration'),
            'aircraft_photo_small': None,
            'aircraft_photo_medium': None,
            'aircraft_photo_large': aircraft.get('url_photo'),
            'aircraft_model': aircraft.get('type'),
            'aircraft_code': aircraft.get('icao_type'),
            'airline': airline.get('name'),
            # adsbdb has no distinct "short name" field the way FR24 did - its 'name'
            # field is already short enough for the ticker ("Air Canada", "Porter
            # Airlines"), and the ticker card displays airline_short, not airline.
            'airline_short': airline.get('name'),
            'airline_iata': airline.get('iata'),
            'airline_icao': airline.get('icao'),
            'airport_origin_name': origin.get('name'),
            'airport_origin_code_iata': origin.get('iata_code'),
            'airport_origin_code_icao': origin.get('icao_code'),
            'airport_origin_country_name': origin.get('country_name'),
            'airport_origin_country_code': origin.get('country_iso_name'),
            'airport_origin_city': origin.get('municipality'),
            'airport_origin_timezone_offset': None,
            'airport_origin_timezone_abbr': None,
            'airport_origin_terminal': None,
            'airport_origin_latitude': origin.get('latitude'),
            'airport_origin_longitude': origin.get('longitude'),
            'airport_destination_name': destination.get('name'),
            'airport_destination_code_iata': destination.get('iata_code'),
            'airport_destination_code_icao': destination.get('icao_code'),
            'airport_destination_country_name': destination.get('country_name'),
            'airport_destination_country_code': destination.get('country_iso_name'),
            'airport_destination_city': destination.get('municipality'),
            'airport_destination_timezone_offset': None,
            'airport_destination_timezone_abbr': None,
            'airport_destination_terminal': None,
            'airport_destination_latitude': destination.get('latitude'),
            'airport_destination_longitude': destination.get('longitude'),
            'time_scheduled_departure': None,
            'time_scheduled_arrival': None,
            'time_real_departure': None,
            'time_real_arrival': None,
            'time_estimated_departure': None,
            'time_estimated_arrival': None,
            'latitude': state["latitude"],
            'longitude': state["longitude"],
            'altitude': altitude_ft,
            'heading': state["true_track"],
            'ground_speed': mps_to_knots(state["velocity_mps"]),
            'squawk': state["squawk"],
            'vertical_speed': mps_to_fpm(state["vertical_rate_mps"]),
            'aircraft_icao_24bit': icao24,
            'distance': new_distance,
            'closest_distance': min(
                new_distance,
                previous_closest_distance if previous_closest_distance is not None else new_distance,
            ),
            'on_ground': state["on_ground"],
            # adsbdb has no live timing data (only static route info), so the
            # time_* fields above are always None. Derive a coarse phase from data
            # OpenSky does give us (on_ground + vertical rate) so the ticker still
            # conveys takeoff/landing instead of going silent for every flight.
            'status': flight_phase(state["on_ground"], mps_to_fpm(state["vertical_rate_mps"])),
        }
        flight['aircraft_category'] = "Helicopter" if is_helicopter(flight) else "Airplane"
        self._takeoff_and_landing(flight, last_position, state["on_ground"])
        return flight

    def _takeoff_and_landing(self, flight: dict[str, Any], last_position, position) -> None:
        last_position = to_int(last_position)
        position = to_int(position)
        if last_position is None or position is None or last_position == position:
            return
        if position == 0:
            self._event_manager.add_events(EVENT_AREA_TOOK_OFF, [flight])
        else:
            self._event_manager.add_events(EVENT_AREA_LANDED, [flight])
