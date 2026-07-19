import logging
import re
import time
from typing import Any
from enum import Enum
from FlightRadar24 import FlightRadar24API, Flight, Entity
from .helper import to_int, get_value, haversine_km, meters_to_feet, mps_to_knots, mps_to_fpm
from .event import EventManager
from .opensky import OpenSkyClient
from .adsbdb import AdsbdbClient

_LOGGER = logging.getLogger(__name__)

# FlightRadar24's per-flight detail-lookup endpoint (used below for every flight not
# already cached from a previous successful cycle) enforces a much stricter per-IP
# burst limit than the area/"zones" listing call -- confirmed by direct testing that a
# tight loop of ~10 consecutive get_flight_details() calls with no delay reliably draws
# an HTTP 429 on the 11th, and every call in that same burst after that. Since
# update_flights_in_area() has no per-flight error handling, a single 429 partway
# through aborts the WHOLE method before self._in_area is ever assigned -- meaning a
# large area (lots of flights, all uncached after any coordinator reload/reset) reliably
# nukes every single update cycle indefinitely, never producing any flights at all. A
# small delay between real (non-cached) detail fetches keeps this under whatever the
# actual limit is without losing any data -- it only makes a cold cycle take longer,
# which is fine since this runs in HA's executor thread pool, off the event loop.
DETAIL_FETCH_THROTTLE_S = 0.8

# Hard ceiling on total wall-clock time update_flights_in_area() will spend making REAL
# (non-cached) detail fetches in a single cycle. DETAIL_FETCH_THROTTLE_S keeps any one
# burst from tripping FlightRadar24's rate limit, but does nothing to bound the TOTAL
# cycle duration when many flights are uncached at once (a naturally busy radius, or
# every flight right after a coordinator reload) -- confirmed live: a ~9-minute full HA
# freeze (the entire frontend stopped responding, even a fresh page load) traced back to
# exactly this. Kept comfortably under the 10s "slow entity update" watchdog threshold
# HA itself warns about. Once the budget is spent, remaining flights this cycle reuse
# their last known detail data if we have any (see the allow_real_fetch fallback in
# _update_flights_data) rather than making a fresh call or being dropped outright --
# only genuinely new, never-before-seen flights get skipped for one cycle when the
# budget runs out, same as an individual fetch failure already was.
DETAIL_FETCH_TIME_BUDGET_S = 6.0

# FlightRadar24's area/"zones" listing endpoint (the OTHER call this file makes --
# get_flights(bounds=...), separate from the per-flight detail endpoint throttled above)
# has its own, separate soft-block behavior: instead of an HTTP error, a blocked request
# gets a valid 200 response with an empty flight list. Nothing distinguishes that from a
# genuine "no traffic in the area right now" response at this layer. Without this grace
# window, update_flights_in_area() below used to overwrite self._in_area with {} on every
# single blocked cycle, immediately wiping the sensor's flights attribute -- reported live
# as "the card sometimes shows no data" even when there really were nearby flights moments
# earlier. Keep serving the last known-good area list for up to this many seconds since it
# was last genuinely non-empty (flagged via self.area_stale so sensor.py/the dashboard card
# can show it's serving cached data); past this window, trust an empty result as real --
# otherwise a genuine, prolonged lull would show stale flights forever.
AREA_STALE_GRACE_S = 600.0
from ..const import (
    EVENT_ENTRY,
    EVENT_EXIT,
    EVENT_AREA_LANDED,
    EVENT_AREA_TOOK_OFF,
    EVENT_TRACKED_LANDED,
    EVENT_TRACKED_TOOK_OFF,
    EVENT_MOST_TRACKED_NEW,
    EVENT_TRACKED_ARRIVED_GATE,
    EVENT_TRACKED_LEFT_GATE,
)
import pycountry


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


class FlightType(Enum):
    TRACKED = 1
    IN_AREA = 2


class FlightProcessor:
    __slots__ = ('_in_area', '_tracked', '_most_tracked', '_entered', '_exited', '_min_altitude', '_max_altitude',
                 '_point', '_client', '_bounds', '_event_manager', '_auto_cleanup',
                 '_last_nonempty_monotonic', '_area_stale',
                 '_last_most_tracked_nonempty_monotonic', '_most_tracked_stale',
                 '_opensky', '_adsbdb', '_opensky_bbox')

    def __init__(
            self,
            client: FlightRadar24API,
            event_manager: EventManager,
            min_altitude: int,
            max_altitude: int,
            point: Entity,
            bounds: str,
            auto_cleanup: bool = False,
            opensky_client: OpenSkyClient | None = None,
            adsbdb_client: AdsbdbClient | None = None,
            opensky_bbox: tuple[float, float, float, float] | None = None,
    ) -> None:
        self._min_altitude = min_altitude
        self._max_altitude = max_altitude
        self._point = point
        self._client = client
        self._bounds = bounds
        self._event_manager = event_manager
        self._auto_cleanup = auto_cleanup
        # Area/"zones" in-area feed now sources from OpenSky (positions) + adsbdb
        # (route/aircraft enrichment) instead of FlightRadar24 when configured -
        # see update_flights_in_area() below. self._client (FlightRadar24API) is
        # still used for update_flights_tracked/update_most_tracked/_find_flight,
        # which have no OpenSky/adsbdb equivalent and aren't on the live
        # dashboard, so they're left on FR24 rather than dropped.
        self._opensky = opensky_client
        self._adsbdb = adsbdb_client
        self._opensky_bbox = opensky_bbox
        self._in_area: dict[str, dict[str, Any]] | None = None
        self._tracked: dict[str, dict[str, Any]] = {}
        self._most_tracked: dict[str, dict[str, Any]] | None = None
        self._entered: list[dict[str, Any]] = []
        self._exited: list[dict[str, Any]] = []
        self._last_nonempty_monotonic: float | None = None
        self._area_stale: bool = False
        self._last_most_tracked_nonempty_monotonic: float | None = None
        self._most_tracked_stale: bool = False

    @property
    def tracked(self) -> dict[str, dict[str, Any]]:
        return self._tracked

    @property
    def tracked_list(self) -> list[dict[str, Any]]:
        return list(self._tracked.values()) if self._tracked else []

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
    def most_tracked_list(self) -> list[dict[str, Any]] | None:
        return list(self._most_tracked.values()) if self._most_tracked else None

    @property
    def most_tracked_stale(self) -> bool:
        # Same meaning as area_stale above, for the separate "most tracked" endpoint --
        # True while update_most_tracked() is serving a cached list because the most
        # recent get_most_tracked() call came back empty and is suspected to be a soft
        # block rather than genuinely zero tracked flights. See AREA_STALE_GRACE_S.
        return self._most_tracked_stale

    @property
    def entered_list(self) -> list[dict[str, Any]]:
        return self._entered

    @property
    def exited_list(self) -> list[dict[str, Any]]:
        return self._exited

    def clear_live_data(self) -> None:
        self._in_area = {}
        self._most_tracked = {}
        self._entered = []
        self._exited = []
        self._tracked = {key: {
            'aircraft_registration': value.get('aircraft_registration'),
            'flight_number': value.get('flight_number'),
            'callsign': value.get('callsign'),
        } for key, value in self._tracked.items()}

    def clear_tracked(self) -> None:
        self._tracked = {}

    def set_tracked(self, tracked: dict[str, dict[str, Any]]) -> None:
        self._tracked = tracked

    def set_in_area(self, in_area: dict[str, dict[str, Any]]) -> None:
        # Mirrors set_tracked() above, but ALSO seeds _last_nonempty_monotonic (and
        # clears _area_stale) -- called only from sensor.py's FlightRadar24RestoreSensor
        # at HA startup, to close a DIFFERENT gap than AREA_STALE_GRACE_S normally
        # covers. AREA_STALE_GRACE_S protects an already-warm self._in_area from a
        # transient soft block; a cold start has nothing to protect at all
        # (self._in_area starts empty, self._last_nonempty_monotonic starts None), so
        # the very first post-restart poll -- if it happens to be soft-blocked -- wipes
        # straight to empty with zero grace window. Confirmed live: after an HA restart,
        # sensor.flightradar24_current_in_area came back empty with stale=false and
        # STAYED that way for several minutes with a real flight independently confirmed
        # present. Restoring the last known flight list from the entity's recorded state
        # AND stamping _last_nonempty_monotonic as "now" gives that first post-restart
        # poll the same grace-window protection a warm cycle would have had.
        self._in_area = in_area
        self._last_nonempty_monotonic = time.monotonic()
        self._area_stale = False

    def enable_most_tracked(self) -> None:
        self._most_tracked = {}

    def add_track(self, number: str) -> dict | None:
        found: dict[str, dict[str, Any]] = {}
        number = number.upper()
        self._find_flight(found, number)
        if not found:
            return None
        self._tracked = self._tracked | found if self._tracked else found

        return found

    def remove_track(self, number: str) -> dict | None:
        number = number.upper()
        for flight_id, flight in self._tracked.items():
            if (number == flight.get('aircraft_registration') or
                    number == flight.get('flight_number') or
                    number == flight.get('callsign')):
                return self._tracked.pop(flight_id)
        return None

    def update_flights_in_area(self) -> None:
        self._entered = {}
        self._exited = {}

        if self._opensky is not None and self._opensky_bbox is not None:
            current = self._update_flights_in_area_opensky()
        else:
            current = self._update_flights_in_area_fr24()

        if current is None:
            # Suspected soft block / transient failure - the grace-window branch
            # inside whichever backend ran above already logged it and left
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
        the in-area feed - replaces the FlightRadar24 zones/detail calls below,
        which were the endpoint that kept getting soft-blocked. Returns None to
        mean "treat this cycle as a suspected transient failure, keep serving
        cached data" - same AREA_STALE_GRACE_S contract the FR24 path used.
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
                    # Same reasoning as the FR24 path's per-flight try/except below:
                    # one flight's adsbdb enrichment call misbehaving (unexpected
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
            'airline_short': None,
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
        }
        flight['aircraft_category'] = "Helicopter" if is_helicopter(flight) else "Airplane"
        self._takeoff_and_landing(flight, last_position, state["on_ground"], FlightType.IN_AREA)
        return flight

    def _update_flights_in_area_fr24(self) -> dict[str, dict[str, Any]] | None:
        """Original FlightRadar24-backed in-area feed. Kept as a fallback path
        for when OpenSky credentials aren't configured (self._opensky is None)
        - not used in normal operation once OpenSky/adsbdb are set up, since
        this is the exact endpoint that kept getting soft-blocked.
        """
        flights = self._client.get_flights(bounds=self._bounds)

        if not flights and self._in_area and self._last_nonempty_monotonic is not None and (
            time.monotonic() - self._last_nonempty_monotonic < AREA_STALE_GRACE_S
        ):
            # Suspected soft block (see AREA_STALE_GRACE_S above): leave self._in_area
            # completely untouched -- from our perspective nothing genuinely changed,
            # so no flight should be reported as having "exited" just because this one
            # cycle's fetch came back empty. Only a non-suspect empty result (below,
            # once the grace window has elapsed) legitimately means everyone left.
            if not self._area_stale:
                _LOGGER.warning(
                    "Area/zones flight feed returned empty; suspected soft block "
                    "(FlightRadar24 anti-bot), serving cached in-area flight list "
                    "for up to %.0fs since last real data", AREA_STALE_GRACE_S,
                )
            self._area_stale = True
            return None

        if self._area_stale and flights:
            _LOGGER.info("Area/zones flight feed recovered after suspected soft block")
        self._area_stale = False
        current: dict[str, dict[str, Any]] = {}
        if flights:
            self._last_nonempty_monotonic = time.monotonic()
            budget_deadline = time.monotonic() + DETAIL_FETCH_TIME_BUDGET_S
            for obj in flights:
                if not self._min_altitude <= obj.altitude <= self._max_altitude:
                    continue
                try:
                    self._update_flights_data(
                        obj, current, self._in_area, FlightType.IN_AREA,
                        allow_real_fetch=time.monotonic() < budget_deadline,
                    )
                except Exception:
                    # One flight's detail fetch failing (429, transient network error, etc.)
                    # used to abort this ENTIRE method before self._in_area was ever
                    # reassigned -- discarding every flight already successfully processed
                    # earlier in this same loop, not just the one that failed. Skipping
                    # just the one flight (it'll be retried next cycle) keeps the rest.
                    _LOGGER.warning("%s: skipping flight (detail fetch failed)", obj.id, exc_info=True)
                    continue
        else:
            self._last_nonempty_monotonic = None

        return current

    def update_flights_tracked(self) -> None:
        if not self._tracked:
            return

        reg_numbers = []
        current_flights = []
        current: dict[str, dict[str, Any]] = {}
        for flight in self._tracked:
            if self._tracked[flight].get('aircraft_registration'):
                reg_numbers.append(self._tracked[flight].get('aircraft_registration'))

        if reg_numbers:
            flights = self._client.get_flights(registration=','.join(reg_numbers))
            for obj in flights:
                self._update_flights_data(obj, current, self._tracked, FlightType.TRACKED)
                current[obj.id]['tracked_type'] = 'live'
                if current[obj.id].get('flight_number'):
                    current_flights.append(current[obj.id].get('flight_number'))
                if current[obj.id].get('callsign'):
                    current_flights.append(current[obj.id].get('callsign'))

        remains = self._tracked.keys() - current.keys()
        if remains:
            for flight_id in remains:
                flight_number = self._tracked[flight_id].get('flight_number')
                if flight_number and flight_number in current_flights:
                    continue
                callsign = self._tracked[flight_id].get('callsign')
                if not flight_number and callsign and callsign in current_flights:
                    continue
                number = flight_number or callsign
                if not number:
                    continue
                size = current.__len__()
                self._find_flight(current, number)
                if size != current.__len__():
                    current_flights.append(number)
                else:
                    current[flight_id] = self._tracked[flight_id]
                    current[flight_id]['tracked_type'] = 'not_found'

        # --- AUTO-CLEANUP LOGIC WRAPPED IN CONFIG CHECK ---
        if self._auto_cleanup:
            keys_to_remove = []
            for fid, new_data in current.items():
                if new_data.get('tracked_type') == 'schedule':
                    number = new_data.get('flight_number') or new_data.get('callsign')
                    # Check if this flight was 'live' in our previous update
                    for old_data in self._tracked.values():
                        old_number = old_data.get('flight_number') or old_data.get('callsign')
                        if old_number == number and old_data.get('tracked_type') == 'live':
                            keys_to_remove.append(fid)
                            # Fire an event to Home Assistant for automations!
                            self._event_manager.add_events(EVENT_TRACKED_ARRIVED_GATE, [old_data])
                            break

            # Remove the landed flights from the current tracking list
            for fid in keys_to_remove:
                current.pop(fid, None)
        # -------------------------------------------------------------------

        # --- LEFT GATE LOGIC ---
        # Fire an event when a flight changes from 'schedule' to 'live'
        # Issue context: https://github.com/AlexandrErohin/home-assistant-flightradar24/issues/171
        for fid, new_data in current.items():
            if new_data.get('tracked_type') == 'live':
                number = new_data.get('flight_number') or new_data.get('callsign')
                for old_data in self._tracked.values():
                    old_number = old_data.get('flight_number') or old_data.get('callsign')
                    if old_number == number and old_data.get('tracked_type') == 'schedule':
                        self._event_manager.add_events(EVENT_TRACKED_LEFT_GATE, [new_data])
                        break
        # -----------------------

        self._tracked = current

    def _find_flight(self, current: dict[str, dict[str, Any]], number: str) -> None:
        def process_search_flight(objects: dict, search: str) -> dict | None:
            live = objects.get('live')
            if live:
                for element in live:
                    detail = element.get('detail')
                    if detail and search in (detail.get('reg'), detail.get('callsign'), detail.get('flight')):
                        return element
            schedule = objects.get('schedule')
            if schedule:
                for element in schedule:
                    detail = element.get('detail')
                    if detail and search in (detail.get('callsign'), detail.get('flight')):
                        return element
            return None

        flights = self._client.search(number)
        found = process_search_flight(flights, number)
        if not found:
            return
        if found.get('type') == 'live':
            data = [None] * 20
            data[1] = get_value(found, ['detail', 'lat'])
            data[2] = get_value(found, ['detail', 'lon'])
            data[13] = []
            flight = Flight(found.get('id'), data)
            flight.registration = found['detail']['reg']
            flight.callsign = found['detail']['callsign']

            self._update_flights_data(flight, current, self._tracked)
        else:
            current[found.get('id')] = {
                'id': found.get('id'),
                'callsign': found['detail'].get('callsign'),
                'flight_number': found['detail'].get('flight'),
                'aircraft_registration': None,
            }
        current[found.get('id')]['tracked_type'] = found.get('type')

    def update_most_tracked(self) -> None:
        if self._most_tracked is None:
            return
        flights = self._client.get_most_tracked()
        data = flights.get('data') if flights else None

        if not data and self._most_tracked and self._last_most_tracked_nonempty_monotonic is not None and (
            time.monotonic() - self._last_most_tracked_nonempty_monotonic < AREA_STALE_GRACE_S
        ):
            # Same soft-block failure mode as update_flights_in_area() above (see the
            # AREA_STALE_GRACE_S comment): FlightRadar24's "most tracked" endpoint can
            # also return a valid response with an empty/missing 'data' list instead of
            # an error. This used to unconditionally fall through to
            # `self._most_tracked = current`, silently wiping the most_tracked sensor's
            # flights every blocked cycle -- the exact same latent bug
            # update_flights_in_area() had before today's fix, just never hit because
            # nothing on the live dashboard currently displays most_tracked data.
            # Reusing AREA_STALE_GRACE_S rather than a separate constant: this endpoint's
            # soft-block behavior and consequences are identical to the area/zones one,
            # so there's no reason for its grace window to differ.
            if not self._most_tracked_stale:
                _LOGGER.warning(
                    "Most-tracked flight feed returned empty; suspected soft block "
                    "(FlightRadar24 anti-bot), serving cached most-tracked list "
                    "for up to %.0fs since last real data", AREA_STALE_GRACE_S,
                )
            self._most_tracked_stale = True
            return

        if self._most_tracked_stale and data:
            # Only log "recovered" when real (non-empty) data actually came back, not
            # when we merely gave up caching because the grace window expired on a
            # still-empty response.
            _LOGGER.info("Most-tracked flight feed recovered after suspected soft block")
        self._most_tracked_stale = False

        current: dict[str, dict[str, Any]] = {}
        for obj in (data or []):
            current[obj['flight_id']] = {
                'id': obj.get('flight_id'),
                'flight_number': obj.get('flight'),
                'callsign': obj.get('callsign'),
                'squawk': obj.get('squawk'),
                'clicks': obj.get('clicks'),
                'airport_origin_code_iata': obj.get('from_iata'),
                'airport_origin_city': obj.get('from_city'),
                'airport_destination_code_iata': obj.get('to_iata'),
                'airport_destination_city': obj.get('to_city'),
                'aircraft_code': obj.get('model'),
                'aircraft_model': obj.get('type'),
                'on_ground': obj.get('on_ground'),
            }

        if data:
            self._last_most_tracked_nonempty_monotonic = time.monotonic()
        else:
            self._last_most_tracked_nonempty_monotonic = None

        entries = [current[x] for x in (current.keys() - self._most_tracked.keys())]
        self._most_tracked = current
        self._event_manager.add_events(EVENT_MOST_TRACKED_NEW, entries)

    def _update_flights_data(self,
                             obj: Flight,
                             current: dict[str, dict[str, Any]],
                             tracked: dict[str, dict[str, Any]],
                             sensor_type: FlightType | None = None,
                             allow_real_fetch: bool = True,
                             ) -> None:
        previous_flight = tracked.get(obj.id) if tracked is not None else None
        last_position = previous_flight.get('on_ground') if previous_flight is not None else None
        previous_closest_distance = (
            previous_flight.get('closest_distance') if previous_flight is not None else None
        )
        if (tracked is not None and obj.id in tracked and self._is_valid(tracked[obj.id])
                and to_int(last_position) == obj.on_ground):
            flight = tracked[obj.id]
        elif not allow_real_fetch and previous_flight is not None and self._is_valid(previous_flight):
            # This cycle's detail-fetch time budget (DETAIL_FETCH_TIME_BUDGET_S) is
            # already spent. Reuse whatever detail data we have for this flight rather
            # than making a fresh (slow, rate-limited) call or dropping it outright --
            # the live positional fields below get refreshed from `obj` regardless, so
            # this only leaves route/aircraft-type/etc. up to one cycle stale, which is
            # a much better trade than the multi-minute freeze this budget exists to
            # prevent. A flight we've genuinely never seen before (no previous_flight)
            # still falls through to the real fetch below -- there's nothing to reuse.
            flight = previous_flight
        else:
            # Throttle real (non-cached) detail fetches -- see DETAIL_FETCH_THROTTLE_S
            # comment at the top of this file. This runs in HA's executor thread pool,
            # not the event loop, so a plain sleep here doesn't block anything else.
            # Deliberately in `finally`, not just after a successful call: a FAILED
            # fetch (e.g. still-active 429) must be throttled too, or a string of
            # failures retries with zero delay between them -- hammering an endpoint
            # that's already rate-limiting us, which can only make things worse.
            try:
                data = self._client.get_flight_details(obj)
            finally:
                time.sleep(DETAIL_FETCH_THROTTLE_S)
            flight = self._get_flight_data(data)
        if flight is not None:
            current[flight['id']] = flight
            flight['latitude'] = obj.latitude
            flight['longitude'] = obj.longitude
            flight['altitude'] = obj.altitude
            flight['heading'] = obj.heading
            flight['ground_speed'] = obj.ground_speed
            flight['squawk'] = obj.squawk
            flight['vertical_speed'] = obj.vertical_speed
            flight['aircraft_icao_24bit'] = getattr(obj, 'icao_24bit', '')
            new_distance = obj.get_distance_from(self._point)
            flight['distance'] = new_distance
            flight['closest_distance'] = min(
                new_distance,
                previous_closest_distance if previous_closest_distance is not None else new_distance,
            )
            flight['on_ground'] = obj.on_ground
            flight['aircraft_category'] = "Helicopter" if is_helicopter(flight) else "Airplane"
            self._takeoff_and_landing(flight, last_position, obj.on_ground, sensor_type)

    def _takeoff_and_landing(self,
                             flight: dict[str, Any],
                             last_position, position,
                             sensor_type: FlightType | None) -> None:
        last_position = to_int(last_position)
        position = to_int(position)
        if sensor_type is None or last_position is None or position is None or last_position == position:
            return
        if position == 0:
            self._event_manager.add_events(EVENT_AREA_TOOK_OFF if FlightType.IN_AREA == sensor_type
                                           else EVENT_TRACKED_TOOK_OFF, [flight])
        else:
            self._event_manager.add_events(EVENT_AREA_LANDED if FlightType.IN_AREA == sensor_type
                                           else EVENT_TRACKED_LANDED, [flight])

    def _get_flight_data(self, flight: dict) -> dict[str, Any] | None:

        def _get_country_code(code: None | str) -> None | str:
            if code is None or len(code) == 2:
                return code

            country = pycountry.countries.get(alpha_3=code)

            return country.alpha_2 if country is not None else code

        flight_id = get_value(flight, ['identification', 'id'])
        if flight_id is None:
            return None

        return {
            'id': flight_id,
            'flight_number': get_value(flight, ['identification', 'number', 'default']),
            'callsign': get_value(flight, ['identification', 'callsign']),
            'aircraft_registration': get_value(flight, ['aircraft', 'registration']),
            'aircraft_photo_small': get_value(flight, ['aircraft', 'images', 'thumbnails', 0, 'src']),
            'aircraft_photo_medium': get_value(flight, ['aircraft', 'images', 'medium', 0, 'src']),
            'aircraft_photo_large': get_value(flight, ['aircraft', 'images', 'large', 0, 'src']),
            'aircraft_model': get_value(flight, ['aircraft', 'model', 'text']),
            'aircraft_code': get_value(flight, ['aircraft', 'model', 'code']),
            'airline': get_value(flight, ['airline', 'name']),
            'airline_short': get_value(flight, ['airline', 'short']),
            'airline_iata': get_value(flight, ['airline', 'code', 'iata']),
            'airline_icao': get_value(flight, ['airline', 'code', 'icao']),
            'airport_origin_name': get_value(flight, ['airport', 'origin', 'name']),
            'airport_origin_code_iata': get_value(flight, ['airport', 'origin', 'code', 'iata']),
            'airport_origin_code_icao': get_value(flight, ['airport', 'origin', 'code', 'icao']),
            'airport_origin_country_name': get_value(flight, ['airport', 'origin', 'position', 'country', 'name']),
            'airport_origin_country_code': _get_country_code(
                get_value(flight, ['airport', 'origin', 'position', 'country', 'code'])),
            'airport_origin_city': get_value(flight, ['airport', 'origin', 'position', 'region', 'city']),
            'airport_origin_timezone_offset': get_value(flight, ['airport', 'origin', 'timezone', 'offset']),
            'airport_origin_timezone_abbr': get_value(flight, ['airport', 'origin', 'timezone', 'abbr']),
            'airport_origin_terminal': get_value(flight, ['airport', 'origin', 'info', 'terminal']),
            'airport_origin_latitude': get_value(flight, ['airport', 'origin', 'position', 'latitude']),
            'airport_origin_longitude': get_value(flight, ['airport', 'origin', 'position', 'longitude']),
            'airport_destination_name': get_value(flight, ['airport', 'destination', 'name']),
            'airport_destination_code_iata': get_value(flight, ['airport', 'destination', 'code', 'iata']),
            'airport_destination_code_icao': get_value(flight, ['airport', 'destination', 'code', 'icao']),
            'airport_destination_country_name': get_value(flight, ['airport', 'destination', 'position',
                                                                   'country', 'name']),
            'airport_destination_country_code': _get_country_code(
                get_value(flight, ['airport', 'destination', 'position', 'country', 'code'])),
            'airport_destination_city': get_value(flight, ['airport', 'destination', 'position',
                                                           'region', 'city']),
            'airport_destination_timezone_offset': get_value(flight,
                                                             ['airport', 'destination', 'timezone', 'offset']),
            'airport_destination_timezone_abbr': get_value(flight, ['airport', 'destination', 'timezone', 'abbr']),
            'airport_destination_terminal': get_value(flight, ['airport', 'destination', 'info', 'terminal']),
            'airport_destination_latitude': get_value(flight, ['airport', 'destination', 'position', 'latitude']),
            'airport_destination_longitude': get_value(flight, ['airport', 'destination', 'position', 'longitude']),
            'time_scheduled_departure': get_value(flight, ['time', 'scheduled', 'departure']),
            'time_scheduled_arrival': get_value(flight, ['time', 'scheduled', 'arrival']),
            'time_real_departure': get_value(flight, ['time', 'real', 'departure']),
            'time_real_arrival': get_value(flight, ['time', 'real', 'arrival']),
            'time_estimated_departure': get_value(flight, ['time', 'estimated', 'departure']),
            'time_estimated_arrival': get_value(flight, ['time', 'estimated', 'arrival']),
        }

    # --- IMPLEMENTED ALEXANDR'S FIX HERE SO I DONT FORGET ---
    def _is_valid(self, flight: dict) -> bool:
        if is_helicopter(flight):
            return flight.get("id") is not None

        required_fields = [
            "flight_number",
            "time_scheduled_departure",
            "time_estimated_arrival",
        ]

        return all(flight.get(f) is not None for f in required_fields)
