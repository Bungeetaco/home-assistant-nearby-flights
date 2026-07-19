import logging
import time
from enum import Enum
from FlightRadar24 import FlightRadar24API
from .helper import to_int, to_float, get_value
from typing import Any

_LOGGER = logging.getLogger(__name__)

# FlightRadar24's airport-details endpoint (get_airport_details(), used below) is fronted
# by the same Cloudflare-based anti-bot layer as the area/"zones" listing endpoint in
# flight.py -- a soft-blocked request comes back as a valid response missing the expected
# ['airport', 'pluginData'] payload (get_value(...) below then returns None) instead of an
# HTTP error. Without this grace window, update_airport_info() below used to overwrite
# self._stats/_arrivals/_departures unconditionally on every blocked cycle, wiping the
# airport sensors' data even though nothing genuinely changed -- the exact same latent bug
# flight.py's update_flights_in_area() had before its same-day fix (see AREA_STALE_GRACE_S
# in flight.py), just never hit here because nothing on the live dashboard currently
# displays this data. Kept as its own constant (rather than importing AREA_STALE_GRACE_S
# from flight.py) to avoid an otherwise-unneeded cross-module dependency between the two
# api/ files -- the two endpoints' soft-block behavior and consequences are equivalent, so
# the same 600s (10 min) duration is used; there's no airport-specific reason to differ.
AIRPORT_STALE_GRACE_S = 600.0


class ScheduleType(Enum):
    ARRIVAL = 1
    DEPARTURE = 2


class AirportStats:
    arrivals_on_time: int
    arrivals_delayed: int
    arrivals_delay_average: int
    arrivals_delay_index: float
    arrivals_canceled: int
    departures_on_time: int
    departures_delayed: int
    departures_delay_average: int
    departures_delay_index: float
    departures_canceled: int


class AirportProcessor:
    __slots__ = ('_client', '_code', '_stats', '_arrivals', '_departures',
                 '_last_nonempty_monotonic', '_stale')

    def __init__(self, client: FlightRadar24API) -> None:
        self._client = client
        self._code: str | None = None
        self._stats: AirportStats | None = None
        self._arrivals: list[dict[str, Any]] | None = None
        self._departures: list[dict[str, Any]] | None = None
        self._last_nonempty_monotonic: float | None = None
        self._stale: bool = False

    @property
    def code(self) -> str | None:
        return self._code

    @property
    def stats(self) -> AirportStats | None:
        return self._stats

    @property
    def arrivals(self) -> list[dict[str, Any]]:
        return self._arrivals

    @property
    def departures(self) -> list[dict[str, Any]]:
        return self._departures

    @property
    def stale(self) -> bool:
        # True while update_airport_info() is serving cached stats/arrivals/departures
        # because the most recent get_airport_details() call came back without the
        # expected payload and is suspected to be a soft block rather than a genuine
        # data change. See AIRPORT_STALE_GRACE_S above.
        return self._stale

    def set_track(self, code: str) -> None:
        code = code.upper()
        self.update_airport_info(code)
        self._code = code

    def restore_code(self, code: str) -> None:
        code = code.upper()
        self._code = code

    def remove_track(self) -> None:
        self._code = None
        self._stats = None
        self._arrivals = None
        self._departures = None
        self._last_nonempty_monotonic = None
        self._stale = False

    def clear_live_data(self) -> None:
        self._stats = None
        self._arrivals = None
        self._departures = None
        self._last_nonempty_monotonic = None
        self._stale = False

    def update_airport_info(self, code: str = None) -> None:
        if not self._code and not code:
            return

        data = get_value(self._client.get_airport_details(code or self._code), ['airport', 'pluginData'])

        if not data and self._stats is not None and self._last_nonempty_monotonic is not None and (
            time.monotonic() - self._last_nonempty_monotonic < AIRPORT_STALE_GRACE_S
        ):
            # Suspected soft block (see AIRPORT_STALE_GRACE_S above): leave
            # self._stats/_arrivals/_departures completely untouched instead of wiping
            # them, and skip the log-only-once-per-transition warning past the first
            # cycle of a run of blocked calls.
            if not self._stale:
                _LOGGER.warning(
                    "Airport details feed for %s returned empty; suspected soft block "
                    "(FlightRadar24 anti-bot), serving cached stats/arrivals/departures "
                    "for up to %.0fs since last real data",
                    code or self._code, AIRPORT_STALE_GRACE_S,
                )
            self._stale = True
            return

        if self._stale and data:
            # Only log "recovered" when real data actually came back, not when we
            # merely gave up caching because the grace window expired while still
            # empty -- that's not a recovery, just accepting the empty result as real.
            _LOGGER.info("Airport details feed for %s recovered after suspected soft block",
                         code or self._code)
        self._stale = False

        if data:
            self._last_nonempty_monotonic = time.monotonic()
        else:
            self._last_nonempty_monotonic = None

        self._stats = AirportStats()
        stats = get_value(data, ['details', 'stats', 'arrivals'])
        self._stats.arrivals_on_time = to_int(get_value(stats, ['today', 'quantity', 'onTime']))
        self._stats.arrivals_delayed = to_int(get_value(stats, ['today', 'quantity', 'delayed']))
        self._stats.arrivals_canceled = to_int(get_value(stats, ['today', 'quantity', 'canceled']))
        self._stats.arrivals_delay_average = to_int(get_value(stats, ['delayAvg']))
        self._stats.arrivals_delay_index = to_float(get_value(stats, ['delayIndex']))
        stats = get_value(data, ['details', 'stats', 'departures'])
        self._stats.departures_on_time = to_int(get_value(stats, ['today', 'quantity', 'onTime']))
        self._stats.departures_delayed = to_int(get_value(stats, ['today', 'quantity', 'delayed']))
        self._stats.departures_canceled = to_int(get_value(stats, ['today', 'quantity', 'canceled']))
        self._stats.departures_delay_average = to_int(get_value(stats, ['delayAvg']))
        self._stats.departures_delay_index = to_float(get_value(stats, ['delayIndex']))

        self._update_schedule(ScheduleType.ARRIVAL, get_value(data, ['schedule', 'arrivals', 'data']))
        self._update_schedule(ScheduleType.DEPARTURE, get_value(data, ['schedule', 'departures', 'data']))

    def _update_schedule(self, schedule: ScheduleType, data: list) -> None:
        # Pre-existing latent bug found while adding the stale/grace-window handling
        # above (unrelated to it, but adjacent): get_value() returns None, not [], when
        # ['schedule', 'arrivals'/'departures', 'data'] is missing -- which happens on a
        # genuinely-empty/blocked airport-details response once past AIRPORT_STALE_GRACE_S
        # (or on the very first call, with nothing yet cached to protect). Iterating None
        # directly below used to raise TypeError and abort update_airport_info() entirely.
        data = data or []
        flights = []
        airport = 'origin' if schedule == ScheduleType.ARRIVAL else 'destination'
        i = 0
        for item in data:
            i += 1
            item = get_value(item, ['flight'])
            flights.append({
                'status_text': get_value(item, ['status', 'text']),
                'status': get_value(item, ['status', 'generic', 'status', 'text']),
                'flight_id': get_value(item, ['identification', 'id']),
                'flight_number': get_value(item, ['identification', 'number', 'default']),
                'callsign': get_value(item, ['identification', 'callsign']),
                'aircraft_code': get_value(item, ['aircraft', 'model', 'code']),
                'aircraft_model': get_value(item, ['aircraft', 'model', 'text']),
                'aircraft_registration': get_value(item, ['aircraft', 'registration']),
                'airline': get_value(item, ['airline', 'name']),
                'airline_short': get_value(item, ['airline', 'short']),
                'airline_iata': get_value(item, ['airline', 'code', 'iata']),
                'airline_icao': get_value(item, ['airline', 'code', 'icao']),
                'airport_name': get_value(item, ['airport', airport, 'name']),
                'airport_code_iata': get_value(item, ['airport', airport, 'code', 'iata']),
                'airport_code_icao': get_value(item, ['airport', airport, 'code', 'icao']),
                'airport_country_name': get_value(item, ['airport', airport, 'position', 'country', 'name']),
                'airport_country_code': get_value(item, ['airport', airport, 'position', 'country', 'code']),
                'airport_city': get_value(item, ['airport', airport, 'position', 'region', 'city']),
                'time_scheduled_departure': get_value(item, ['time', 'scheduled', 'departure']),
                'time_scheduled_arrival': get_value(item, ['time', 'scheduled', 'arrival']),
                'time_real_departure': get_value(item, ['time', 'real', 'departure']),
                'time_real_arrival': get_value(item, ['time', 'real', 'arrival']),
                'time_estimated_departure': get_value(item, ['time', 'estimated', 'departure']),
                'time_estimated_arrival': get_value(item, ['time', 'estimated', 'arrival']),
            })
            if i == 50:
                break
        if schedule == ScheduleType.ARRIVAL:
            self._arrivals = flights
        else:
            self._departures = flights
