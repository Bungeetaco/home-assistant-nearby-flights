DEFAULT_NAME = "Nearby Flights"
DOMAIN = "nearby_flights"
URL = 'https://opensky-network.org/'

CONF_MIN_ALTITUDE = "min_altitude"
CONF_MAX_ALTITUDE = "max_altitude"

CONF_OPENSKY_CLIENT_ID = "opensky_client_id"
CONF_OPENSKY_CLIENT_SECRET = "opensky_client_secret"

# Defined in api/event.py (so the api/ package imports without homeassistant
# installed, for the test suite); re-exported here for HA-side consumers.
from .api.event import (  # noqa: E402,F401
    EVENT_ENTRY,
    EVENT_EXIT,
    EVENT_AREA_LANDED,
    EVENT_AREA_TOOK_OFF,
)

MIN_ALTITUDE = -1
MAX_ALTITUDE = 100000

# The OpenSky area feed can fail (request error, rate limit, empty response) in a way
# that looks like a genuine "no traffic" response. FlightProcessor.area_stale
# (api/flight.py) flags this by serving cached data instead of erroring out. If that
# condition persists for longer than this many seconds, we surface a HA Repair (see
# coordinator.py) so the block is visible somewhere other than a small dashboard
# indicator. Must not exceed AREA_STALE_GRACE_S (api/flight.py): empty-response
# episodes end when the grace window runs out (the empty result is then trusted as
# real), so a larger threshold would make the Repair unreachable for them. Request-
# failure episodes continue past the grace window (as UpdateFailed cycles) and keep
# feeding this timer.
AREA_STALE_ISSUE_THRESHOLD_S = 10 * 60

# translation_key for the above Repair issue; must match the "issues" key
# in strings.json / translations/en.json.
ISSUE_AREA_STALE = "area_stale"
