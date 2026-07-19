DEFAULT_NAME = "Nearby Flights"
DOMAIN = "nearby_flights"
URL = 'https://opensky-network.org/'

CONF_MIN_ALTITUDE = "min_altitude"
CONF_MAX_ALTITUDE = "max_altitude"

CONF_OPENSKY_CLIENT_ID = "opensky_client_id"
CONF_OPENSKY_CLIENT_SECRET = "opensky_client_secret"

EVENT_ENTRY = f"{DOMAIN}_entry"
EVENT_EXIT = f"{DOMAIN}_exit"
EVENT_AREA_LANDED = f"{DOMAIN}_area_landed"
EVENT_AREA_TOOK_OFF = f"{DOMAIN}_area_took_off"

MIN_ALTITUDE = -1
MAX_ALTITUDE = 100000

# The OpenSky area feed can fail (request error, rate limit, empty response) in a way
# that looks like a genuine "no traffic" response. FlightProcessor.area_stale
# (api/flight.py) flags this by serving cached data instead of erroring out. If that
# condition persists for longer than this many seconds, we surface a HA Repair (see
# coordinator.py) so the block is visible somewhere other than a small dashboard
# indicator. Kept as its own constant so the threshold is easy to find and retune later.
AREA_STALE_ISSUE_THRESHOLD_S = 20 * 60

# translation_key for the above Repair issue; must match the "issues" key
# in strings.json / translations/en.json.
ISSUE_AREA_STALE = "area_stale"
