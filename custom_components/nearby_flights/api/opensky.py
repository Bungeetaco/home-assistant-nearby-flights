import logging
import time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)
STATES_URL = "https://opensky-network.org/api/states/all"

# Refresh the OAuth2 token this many seconds before its reported expiry, so a
# request never fires with a token that expires mid-flight (OpenSky tokens are
# short-lived, ~1800s at time of writing).
TOKEN_REFRESH_MARGIN_S = 60.0

REQUEST_TIMEOUT_S = 15.0

# Index positions within each element of the states/all "states" array, per
# OpenSky's documented state-vector schema (openskynetwork/opensky-api). Kept
# as named constants rather than magic indices so a future schema change is a
# one-line fix instead of a re-derive-from-docs exercise.
_IDX_ICAO24 = 0
_IDX_CALLSIGN = 1
_IDX_LONGITUDE = 5
_IDX_LATITUDE = 6
_IDX_BARO_ALTITUDE = 7
_IDX_ON_GROUND = 8
_IDX_VELOCITY = 9
_IDX_TRUE_TRACK = 10
_IDX_VERTICAL_RATE = 11
_IDX_GEO_ALTITUDE = 13
_IDX_SQUAWK = 14


class OpenSkyAuthError(Exception):
    """Raised when the OAuth2 client-credentials exchange fails."""


class OpenSkyClient:
    """Minimal OpenSky Network REST client: OAuth2 client-credentials auth +
    the bounding-box `states/all` endpoint. Blocking (uses `requests`), same
    as the FlightRadarAPI calls elsewhere in this integration - callers are
    expected to run it via hass.async_add_executor_job.
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = requests.Session()
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _ensure_token(self) -> str:
        if self._access_token is not None and time.monotonic() < self._token_expires_at:
            return self._access_token

        response = self._session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=REQUEST_TIMEOUT_S,
        )
        if response.status_code != 200:
            raise OpenSkyAuthError(
                f"OpenSky token request failed: HTTP {response.status_code} {response.text[:200]}"
            )

        payload = response.json()
        self._access_token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 1800))
        self._token_expires_at = time.monotonic() + expires_in - TOKEN_REFRESH_MARGIN_S
        return self._access_token

    def validate_credentials(self) -> None:
        """Raise OpenSkyAuthError if the client_id/secret can't get a token.

        Deliberately only exercises the (unmetered) OAuth2 token endpoint, not
        states/all, so validating credentials in the options flow doesn't
        spend any of the daily states/all request quota.
        """
        self._access_token = None
        self._token_expires_at = 0.0
        self._ensure_token()

    def get_states_bbox(
        self, lamin: float, lomin: float, lamax: float, lomax: float
    ) -> list[dict[str, Any]]:
        """Return live state vectors within the given bounding box.

        Raises on any transport/auth/HTTP failure rather than swallowing it -
        the caller (FlightProcessor.update_flights_in_area) is what decides
        whether a failure should fall back to cached data, same contract as
        the FlightRadar24 calls it replaces.
        """
        token = self._ensure_token()
        response = self._session.get(
            STATES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax},
            timeout=REQUEST_TIMEOUT_S,
        )
        if response.status_code == 429:
            retry_after = response.headers.get("X-Rate-Limit-Retry-After-Seconds")
            raise RuntimeError(f"OpenSky rate limit hit (retry after {retry_after}s)")
        response.raise_for_status()

        payload = response.json()
        raw_states = payload.get("states") or []

        states = []
        for row in raw_states:
            try:
                icao24 = row[_IDX_ICAO24]
                latitude = row[_IDX_LATITUDE]
                longitude = row[_IDX_LONGITUDE]
                if icao24 is None or latitude is None or longitude is None:
                    continue
                altitude_m = row[_IDX_BARO_ALTITUDE]
                if altitude_m is None:
                    altitude_m = row[_IDX_GEO_ALTITUDE]
                callsign = (row[_IDX_CALLSIGN] or "").strip()
                states.append(
                    {
                        "icao24": icao24,
                        "callsign": callsign,
                        "latitude": latitude,
                        "longitude": longitude,
                        "altitude_m": altitude_m,
                        "on_ground": bool(row[_IDX_ON_GROUND]),
                        "velocity_mps": row[_IDX_VELOCITY],
                        "true_track": row[_IDX_TRUE_TRACK],
                        "vertical_rate_mps": row[_IDX_VERTICAL_RATE],
                        "squawk": row[_IDX_SQUAWK],
                    }
                )
            except (IndexError, TypeError):
                _LOGGER.debug("OpenSky: skipping malformed state row: %r", row)
                continue

        return states
