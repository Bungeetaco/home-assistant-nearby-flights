from __future__ import annotations
from datetime import timedelta
from time import monotonic
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
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
from .api.opensky import OpenSkyClient
from .api.adsbdb import AdsbdbClient
from .api.helper import Point
from logging import Logger


class NearbyFlightsCoordinator(DataUpdateCoordinator[int]):

    def __init__(
            self,
            hass: HomeAssistant,
            update_interval: int,
            logger: Logger,
            unique_id: str,
            min_altitude: int,
            max_altitude: int,
            point: Point,
            opensky_client: OpenSkyClient,
            adsbdb_client: AdsbdbClient,
            opensky_bbox: tuple[float, float, float, float],
    ) -> None:
        self.unique_id = unique_id
        self.event_manager = EventManager()
        self.flight = FlightProcessor(
            self.event_manager, min_altitude, max_altitude, point,
            opensky_client, adsbdb_client, opensky_bbox,
        )
        self.scanning: bool = True

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
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self):
        if not self.scanning:
            return

        try:
            await self.hass.async_add_executor_job(self.flight.update_flights_in_area)
        except Exception as e:
            self.logger.error("Nearby Flights: %s", e)

        self._update_area_stale_issue()

        def fire(event: Event) -> None:
            self.hass.bus.fire(event.event, event.data)

        self.event_manager.fire_events(self.config_entry.title, fire)

    def _update_area_stale_issue(self) -> None:
        """Raise/clear the "area feed stale" Repair based on FlightProcessor.area_stale.

        area_stale is True while we're serving cached area data because the OpenSky
        request failed or came back empty (see comment in __init__ above /
        api/flight.py). This only escalates to a visible Repair once the condition
        has held continuously for AREA_STALE_ISSUE_THRESHOLD_S - a brief blip
        shouldn't bother anyone.
        """
        issue_id = f"{ISSUE_AREA_STALE}_{self.unique_id}"

        if self.flight.area_stale:
            if self._area_stale_since is None:
                # New continuous stale episode - start the clock.
                self._area_stale_since = monotonic()

            elapsed = monotonic() - self._area_stale_since
            if elapsed >= AREA_STALE_ISSUE_THRESHOLD_S and not self._area_stale_issue_active:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
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
            self._area_stale_since = None
            if self._area_stale_issue_active:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                self._area_stale_issue_active = False
