import logging
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.adsbdb.com/v0"
REQUEST_TIMEOUT_S = 10.0


class AdsbdbClient:
    """adsbdb.com lookups: callsign -> route/airline, icao24 -> aircraft type/
    registration. Free, no-auth, and both facts are effectively static per
    callsign/icao24 (a given flight number's route and a given tail number's
    airframe don't change between polls), so results are cached in-memory for
    the life of this client (i.e. the whole HA run) rather than re-fetched
    every coordinator cycle - keeps steady-state call volume near zero even
    though OpenSky's bulk states/all call happens every scan_interval.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._route_cache: dict[str, dict[str, Any] | None] = {}
        self._aircraft_cache: dict[str, dict[str, Any] | None] = {}

    def lookup_callsign(self, callsign: str) -> dict[str, Any] | None:
        callsign = (callsign or "").strip()
        if not callsign:
            return None
        if callsign in self._route_cache:
            return self._route_cache[callsign]

        result = None
        try:
            response = self._session.get(
                f"{BASE_URL}/callsign/{callsign}", timeout=REQUEST_TIMEOUT_S
            )
            if response.status_code == 200:
                result = (response.json().get("response") or {}).get("flightroute")
        except (requests.RequestException, ValueError) as err:
            _LOGGER.debug("adsbdb: callsign lookup failed for %s: %s", callsign, err)

        self._route_cache[callsign] = result
        return result

    def lookup_aircraft(self, icao24: str) -> dict[str, Any] | None:
        icao24 = (icao24 or "").strip().lower()
        if not icao24:
            return None
        if icao24 in self._aircraft_cache:
            return self._aircraft_cache[icao24]

        result = None
        try:
            response = self._session.get(
                f"{BASE_URL}/aircraft/{icao24}", timeout=REQUEST_TIMEOUT_S
            )
            if response.status_code == 200:
                result = (response.json().get("response") or {}).get("aircraft")
        except (requests.RequestException, ValueError) as err:
            _LOGGER.debug("adsbdb: aircraft lookup failed for %s: %s", icao24, err)

        self._aircraft_cache[icao24] = result
        return result
