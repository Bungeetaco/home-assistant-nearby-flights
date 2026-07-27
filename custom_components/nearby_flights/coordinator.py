from __future__ import annotations
from datetime import timedelta
from time import monotonic
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import issue_registry as ir
from .const import (
    DOMAIN,
    URL,
    DEFAULT_NAME,
    AREA_STALE_ISSUE_THRESHOLD_S,
    ISSUE_AREA_STALE,
)
from .api.event import EventManager, Event
from .api.flight import FlightProcessor
from .api.opensky import OpenSkyClient, OpenSkyAuthError
from .api.adsbdb import AdsbdbClient
from .api.helper import Point
from logging import Logger

# Minimum spacing between real OpenSky fetches, regardless of how many card
# instances drive homeassistant.update_entity at us. Without this floor, every
# open dashboard/tab multiplies the metered states/all call volume and silently
# defeats a deliberately long configured scan_interval; with it, N viewers cost
# the same as one. Kept below the card's 30s base poll cadence so a single
# viewer is never throttled.
FETCH_FLOOR_S = 20.0


class NearbyFlightsCoordinator(DataUpdateCoordinator[int]):

    def __init__(
            self,
            hass: HomeAssistant,
            entry: ConfigEntry,
            update_interval: int,
            logger: Logger,
            min_altitude: int,
            max_altitude: int,
            point: Point,
            opensky_client: OpenSkyClient,
            adsbdb_client: AdsbdbClient,
            opensky_bbox: tuple[float, float, float, float],
    ) -> None:
        self.unique_id = entry.entry_id
        self.event_manager = EventManager()
        self._opensky_client = opensky_client
        self._adsbdb_client = adsbdb_client
        self.flight = FlightProcessor(
            self.event_manager, min_altitude, max_altitude, point,
            opensky_client, adsbdb_client, opensky_bbox,
        )
        self.scanning: bool = True
        self._last_fetch_monotonic: float | None = None

        # Bookkeeping for the "area feed stale" HA Repair.
        # See FlightProcessor.area_stale (api/flight.py) for the underlying
        # signal: it goes True while we're serving cached area/"zones" data
        # because the OpenSky request failed or came back empty and is
        # suspected to be a transient issue rather than genuine zero traffic.
        # _area_stale_since is the monotonic timestamp when the *current*
        # continuous stale episode began (None while not stale);
        # _area_stale_issue_active tracks whether we've already raised the
        # Repair for this episode, so we don't call async_create_issue every
        # single update cycle once past the threshold.
        self._area_stale_since: float | None = None
        self._area_stale_issue_active: bool = False

        self.device_info = DeviceInfo(
            configuration_url=URL,
            identifiers={(DOMAIN, self.unique_id)},
            manufacturer=DEFAULT_NAME,
            name=DEFAULT_NAME,
        )

        super().__init__(
            hass,
            logger,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    def close_clients(self) -> None:
        """Close both HTTP sessions. Blocking - call via executor."""
        self._opensky_client.close()
        self._adsbdb_client.close()

    async def _async_update_data(self) -> int:
        if not self.scanning:
            # Scanning is deliberately paused - that's not a stale-feed
            # condition, so don't leave a Repair up promising to auto-resolve
            # while updates can't run.
            self._clear_area_stale_issue()
            return len(self.flight.in_area_list)

        now = monotonic()
        if self._last_fetch_monotonic is not None and now - self._last_fetch_monotonic < FETCH_FLOOR_S:
            # Card-driven update_entity landed inside the fetch floor - serve
            # what we have instead of spending another metered OpenSky call.
            return len(self.flight.in_area_list)
        self._last_fetch_monotonic = now

        try:
            await self.hass.async_add_executor_job(self.flight.update_flights_in_area)
        except OpenSkyAuthError as err:
            raise ConfigEntryAuthFailed(
                f"OpenSky rejected the configured credentials: {err}"
            ) from err
        except Exception as err:
            # Grace-window soft-fails are handled inside FlightProcessor; what
            # reaches here is a real, uncovered failure. Track the stale
            # episode (so the Repair still fires during a long outage), then
            # let HA see the failure - entities go unavailable instead of
            # presenting "0 flights" as genuine data.
            self._update_area_stale_issue()
            raise UpdateFailed(f"OpenSky area update failed: {err}") from err

        self._update_area_stale_issue()

        def fire(event: Event) -> None:
            self.hass.bus.fire(event.event, event.data)

        self.event_manager.fire_events(self.config_entry.title, fire)
        return len(self.flight.in_area_list)

    def _clear_area_stale_issue(self) -> None:
        """End the stale episode and drop its Repair (no-op when absent).

        Deletion is deliberately NOT gated on _area_stale_issue_active: that
        flag doesn't survive config-entry reloads, but the issue registry
        does, so a previous coordinator instance's Repair must be deletable
        by this one.
        """
        self._area_stale_since = None
        ir.async_delete_issue(self.hass, DOMAIN, f"{ISSUE_AREA_STALE}_{self.unique_id}")
        self._area_stale_issue_active = False

    def _update_area_stale_issue(self) -> None:
        """Raise/clear the "area feed stale" Repair based on FlightProcessor.area_stale.

        area_stale is True while we're serving cached area data because the OpenSky
        request failed or came back empty (see comment in __init__ above /
        api/flight.py). This only escalates to a visible Repair once the condition
        has held continuously for AREA_STALE_ISSUE_THRESHOLD_S - a brief blip
        shouldn't bother anyone.
        """
        if self.flight.area_stale:
            if self._area_stale_since is None:
                # New continuous stale episode - start the clock.
                self._area_stale_since = monotonic()

            elapsed = monotonic() - self._area_stale_since
            if elapsed >= AREA_STALE_ISSUE_THRESHOLD_S and not self._area_stale_issue_active:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"{ISSUE_AREA_STALE}_{self.unique_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_AREA_STALE,
                    translation_placeholders={
                        "name": self.config_entry.title,
                        "minutes": str(AREA_STALE_ISSUE_THRESHOLD_S // 60),
                    },
                )
                self._area_stale_issue_active = True
        else:
            # Recovered - clear the episode and any Repair raised for it.
            self._clear_area_stale_issue()
