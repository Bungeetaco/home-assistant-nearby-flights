from __future__ import annotations
from logging import getLogger
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from .const import DOMAIN, ISSUE_AREA_STALE
from .coordinator import NearbyFlightsCoordinator
from .api.opensky import OpenSkyClient
from .api.adsbdb import AdsbdbClient
from .api.helper import bbox_from_point_radius, Point
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
)
from .const import (
    CONF_MIN_ALTITUDE,
    CONF_MAX_ALTITUDE,
    CONF_OPENSKY_CLIENT_ID,
    CONF_OPENSKY_CLIENT_SECRET,
    MIN_ALTITUDE,
    MAX_ALTITUDE,
)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    latitude = entry.data[CONF_LATITUDE]
    longitude = entry.data[CONF_LONGITUDE]

    opensky_client_id = entry.data.get(CONF_OPENSKY_CLIENT_ID)
    opensky_client_secret = entry.data.get(CONF_OPENSKY_CLIENT_SECRET)
    if not opensky_client_id or not opensky_client_secret:
        # Config flow validates these are present at setup time, so this should
        # only trip on a hand-edited/corrupted config entry.
        raise ConfigEntryNotReady("OpenSky client ID/secret are not configured")

    opensky_client = OpenSkyClient(opensky_client_id, opensky_client_secret)
    adsbdb_client = AdsbdbClient()
    opensky_bbox = bbox_from_point_radius(latitude, longitude, entry.data[CONF_RADIUS] / 1000)

    coordinator = NearbyFlightsCoordinator(
        hass,
        entry,
        entry.data[CONF_SCAN_INTERVAL],
        _LOGGER,
        entry.data.get(CONF_MIN_ALTITUDE, MIN_ALTITUDE),
        entry.data.get(CONF_MAX_ALTITUDE, MAX_ALTITUDE),
        Point(latitude, longitude),
        opensky_client,
        adsbdb_client,
        opensky_bbox,
    )

    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if coordinator is not None:
            await hass.async_add_executor_job(coordinator.close_clients)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # The "area feed stale" Repair is keyed on entry_id and lives in the issue
    # registry, which outlives the coordinator - drop it with the entry.
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_AREA_STALE}_{entry.entry_id}")


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
