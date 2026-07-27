import logging
from collections import OrderedDict
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.adsbdb.com/v0"
REQUEST_TIMEOUT_S = 10.0

# Upper bound per cache. A busy-area install sees a few hundred distinct
# callsigns/airframes a day; months of kiosk uptime must not grow the caches
# without limit, so least-recently-used entries are evicted past this cap.
MAX_CACHE_ENTRIES = 5000


class AdsbdbClient:
    """adsbdb.com lookups: callsign -> route/airline, icao24 -> aircraft type/
    registration. Free, no-auth, and both facts are effectively static per
    callsign/icao24 (a given flight number's route and a given tail number's
    airframe don't change between polls), so definitive results are cached
    in-memory (LRU-bounded) for the life of this client rather than re-fetched
    every coordinator cycle - keeps steady-state call volume near zero even
    though OpenSky's bulk states/all call happens every scan_interval.

    Only definitive outcomes are cached: a 200 (parsed data, or a parse
    failure degraded to "no enrichment") and a 404 (adsbdb genuinely doesn't
    know the identifier). Transient failures - network errors, timeouts,
    429s, 5xx - return None WITHOUT caching, so one blip doesn't permanently
    disable enrichment for that flight until HA restarts.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._route_cache: OrderedDict[str, dict[str, Any] | None] = OrderedDict()
        self._aircraft_cache: OrderedDict[str, dict[str, Any] | None] = OrderedDict()

    def close(self) -> None:
        self._session.close()

    def lookup_callsign(self, callsign: str) -> dict[str, Any] | None:
        callsign = (callsign or "").strip()
        if not callsign:
            return None
        return self._lookup(self._route_cache, callsign, f"{BASE_URL}/callsign/{callsign}", "flightroute")

    def lookup_aircraft(self, icao24: str) -> dict[str, Any] | None:
        icao24 = (icao24 or "").strip().lower()
        if not icao24:
            return None
        return self._lookup(self._aircraft_cache, icao24, f"{BASE_URL}/aircraft/{icao24}", "aircraft")

    def _lookup(
        self,
        cache: OrderedDict[str, dict[str, Any] | None],
        key: str,
        url: str,
        response_field: str,
    ) -> dict[str, Any] | None:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]

        try:
            response = self._session.get(url, timeout=REQUEST_TIMEOUT_S)
        except Exception as err:
            # Transient (network/timeout) - do NOT cache; retry next cycle.
            _LOGGER.debug("adsbdb: lookup failed for %s: %s", key, err)
            return None

        if response.status_code == 404:
            # adsbdb definitively doesn't know this identifier - cache the
            # negative so it isn't re-queried every single coordinator cycle.
            result: dict[str, Any] | None = None
        elif response.status_code != 200:
            # 429/5xx and friends are transient - do NOT cache.
            _LOGGER.debug("adsbdb: lookup for %s returned HTTP %s", key, response.status_code)
            return None
        else:
            try:
                result = (response.json().get("response") or {}).get(response_field)
            except Exception as err:
                # Deliberately broad: adsbdb is a third-party API with no formal
                # response-schema guarantee. A 200 with an unexpected shape
                # (e.g. "response" as a string rather than a dict) must degrade
                # to "no enrichment data" here, not raise - losing a flight's
                # route/aircraft-type info is fine, losing its entire position/
                # altitude/speed data because of it is not (see the per-flight
                # try/except around the caller in api/flight.py, which only
                # exists as defense-in-depth for whatever this doesn't catch).
                _LOGGER.debug("adsbdb: unexpected 200 response shape for %s: %s", key, err)
                result = None

        cache[key] = result
        cache.move_to_end(key)
        if len(cache) > MAX_CACHE_ENTRIES:
            cache.popitem(last=False)
        return result
