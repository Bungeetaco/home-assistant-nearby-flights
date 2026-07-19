from __future__ import annotations
from logging import getLogger
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from .const import DOMAIN
from .coordinator import FlightRadar24Coordinator
from .api.opensky import OpenSkyClient
from .api.adsbdb import AdsbdbClient
from .api.helper import bbox_from_point_radius
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from .const import (
    CONF_MIN_ALTITUDE,
    CONF_MAX_ALTITUDE,
    CONF_MOST_TRACKED,
    CONF_MOST_TRACKED_DEFAULT,
    CONF_ENABLE_TRACKER,
    CONF_ENABLE_TRACKER_DEFAULT,
    CONF_OPENSKY_CLIENT_ID,
    CONF_OPENSKY_CLIENT_SECRET,
    MIN_ALTITUDE,
    MAX_ALTITUDE,
)
from FlightRadar24 import FlightRadar24API, Entity

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.BUTTON,
]

_LOGGER = getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    import logging
    logging.getLogger("FlightRadarAPI").setLevel(logging.ERROR)
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)

    client = FlightRadar24API()
    if username and password:
        await hass.async_add_executor_job(client.login, username, password)

    latitude = entry.data[CONF_LATITUDE]
    longitude = entry.data[CONF_LONGITUDE]

    bounds = client.get_bounds_by_point(latitude, longitude, entry.data[CONF_RADIUS])

    opensky_client_id = entry.data.get(CONF_OPENSKY_CLIENT_ID)
    opensky_client_secret = entry.data.get(CONF_OPENSKY_CLIENT_SECRET)
    opensky_client = None
    adsbdb_client = None
    opensky_bbox = None
    if opensky_client_id and opensky_client_secret:
        # In-area feed sources from OpenSky + adsbdb instead of FlightRadar24
        # when these are configured - see FlightProcessor.update_flights_in_area
        # (api/flight.py). Only affects the in-area/"zones" feed; other sensors
        # (tracked flights, most-tracked, airport schedules) stay on FR24, which
        # has no OpenSky/adsbdb equivalent for that data.
        opensky_client = OpenSkyClient(opensky_client_id, opensky_client_secret)
        adsbdb_client = AdsbdbClient()
        opensky_bbox = bbox_from_point_radius(latitude, longitude, entry.data[CONF_RADIUS] / 1000)

    coordinator = FlightRadar24Coordinator(
        hass,
        bounds,
        client,
        entry.data[CONF_SCAN_INTERVAL],
        _LOGGER,
        entry.entry_id,
        entry.data.get(CONF_MIN_ALTITUDE, MIN_ALTITUDE),
        entry.data.get(CONF_MAX_ALTITUDE, MAX_ALTITUDE),
        Entity(latitude, longitude),
        opensky_client=opensky_client,
        adsbdb_client=adsbdb_client,
        opensky_bbox=opensky_bbox,
    )

    if entry.data.get(CONF_MOST_TRACKED, CONF_MOST_TRACKED_DEFAULT):
        coordinator.flight.enable_most_tracked()
    coordinator.enable_tracker = entry.data.get(CONF_ENABLE_TRACKER, CONF_ENABLE_TRACKER_DEFAULT)

    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
