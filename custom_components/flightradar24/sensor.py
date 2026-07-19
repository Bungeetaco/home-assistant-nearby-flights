from dataclasses import dataclass
from collections.abc import Callable
from typing import Any
from homeassistant.components.sensor import (
    SensorStateClass,
    SensorEntity,
    RestoreSensor,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from .const import DOMAIN
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_registry as er  # Imported for migration
from .coordinator import FlightRadar24Coordinator
import datetime
import copy


@dataclass
class FlightRadar24SensorRequiredKeysMixin:
    value: Callable[[FlightRadar24Coordinator], Any]
    attributes: Callable[[FlightRadar24Coordinator], Any] | None


@dataclass
class FlightRadar24SensorEntityDescription(SensorEntityDescription, FlightRadar24SensorRequiredKeysMixin):
    """A class that describes sensor entities."""


SENSOR_TYPES: tuple[FlightRadar24SensorEntityDescription, ...] = (
    FlightRadar24SensorEntityDescription(
        key="entered",
        translation_key="entered",
        icon="mdi:airplane-check",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: len(coord.flight.entered_list),
        attributes=lambda coord: {'flights': coord.flight.entered_list},
    ),
    FlightRadar24SensorEntityDescription(
        key="exited",
        translation_key="exited",
        icon="mdi:airplane-remove",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: len(coord.flight.exited_list),
        attributes=lambda coord: {'flights': coord.flight.exited_list},
    ),
    FlightRadar24SensorEntityDescription(
        key="most_tracked",
        translation_key="most_tracked",
        icon="mdi:airplane-search",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: len(coord.flight.most_tracked_list) if coord.flight.most_tracked_list else None,
        attributes=lambda coord: {
            'flights': coord.flight.most_tracked_list if coord.flight.most_tracked_list else {},
            'stale': coord.flight.most_tracked_stale,
        },
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_arrivals_on_time",
        translation_key="airport_arrivals_on_time",
        icon="mdi:airplane-check",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.arrivals_on_time if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_arrivals_delayed",
        translation_key="airport_arrivals_delayed",
        icon="mdi:airplane-alert",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.arrivals_delayed if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_arrivals_delay_average",
        translation_key="airport_arrivals_delay_average",
        icon="mdi:airplane-clock",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.arrivals_delay_average if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_arrivals_delay_index",
        translation_key="airport_arrivals_delay_index",
        icon="mdi:airplane-clock",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda coord: coord.airport.stats.arrivals_delay_index if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_arrivals_canceled",
        translation_key="airport_arrivals_canceled",
        icon="mdi:airplane-remove",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.arrivals_canceled if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_arrivals",
        translation_key="airport_arrivals",
        icon="mdi:airplane-landing",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: len(coord.airport.arrivals) if coord.airport.arrivals is not None else None,
        attributes=lambda coord: (
            {'flights': coord.airport.arrivals, 'stale': coord.airport.stale}
            if coord.airport.arrivals is not None else None
        ),
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_departures_on_time",
        translation_key="airport_departures_on_time",
        icon="mdi:airplane-check",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.departures_on_time if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_departures_delayed",
        translation_key="airport_departures_delayed",
        icon="mdi:airplane-alert",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.departures_delayed if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_departures_delay_average",
        translation_key="airport_departures_delay_average",
        icon="mdi:airplane-clock",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.departures_delay_average if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_departures_delay_index",
        translation_key="airport_departures_delay_index",
        icon="mdi:airplane-clock",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda coord: coord.airport.stats.departures_delay_index if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_departures_canceled",
        translation_key="airport_departures_canceled",
        icon="mdi:airplane-remove",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: coord.airport.stats.departures_canceled if coord.airport.stats else None,
        attributes=None,
    ),
    FlightRadar24SensorEntityDescription(
        key="airport_departures",
        translation_key="airport_departures",
        icon="mdi:airplane-takeoff",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: (
            len(coord.airport.departures)
            if coord.airport.departures is not None
            else None
        ),
        attributes=lambda coord: (
            {"flights": coord.airport.departures, "stale": coord.airport.stale}
            if coord.airport.departures is not None
            else None
        ),
    ),
)

RESTORE_SENSOR_TYPES: tuple[FlightRadar24SensorEntityDescription, ...] = (
    FlightRadar24SensorEntityDescription(
        key="additional_tracked",
        translation_key="additional_tracked",
        icon="mdi:airplane",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: len(coord.flight.tracked_list),
        attributes=lambda coord: {"flights": coord.flight.tracked_list},
    ),
    # MOVED HERE FROM SENSOR_TYPES (was a plain, non-restoring FlightRadar24Sensor) --
    # confirmed by reading async_setup_entry() below that unique_id is derived purely
    # from description.key (f"{entry_id}_{DOMAIN}_{description.key}"), and the
    # migration loop iterates SENSOR_TYPES + RESTORE_SENSOR_TYPES together, so moving
    # this tuple doesn't touch entity identity or the migration path at all -- only
    # which base class (and therefore restore behavior) backs it.
    #
    # Reason for the move: on a fresh HA restart, FlightProcessor._in_area starts empty
    # and _last_nonempty_monotonic starts None, so the AREA_STALE_GRACE_S caching logic
    # in update_flights_in_area() has NOTHING to protect for the first cycle(s) --
    # confirmed live, sensor.flightradar24_current_in_area came back empty (stale=false)
    # and stayed that way for several minutes post-restart even with a real flight
    # independently confirmed present, because a soft-blocked first poll had no cached
    # data to fall back to. Restoring the last known flight list (see
    # FlightRadar24RestoreSensor.async_added_to_hass() below) closes that gap the same
    # way additional_tracked's restore already does for tracked flights.
    FlightRadar24SensorEntityDescription(
        key="in_area",
        translation_key="in_area",
        icon="mdi:airplane-marker",
        state_class=SensorStateClass.TOTAL,
        value=lambda coord: len(coord.flight.in_area_list),
        attributes=lambda coord: {'flights': coord.flight.in_area_list, 'stale': coord.flight.area_stale},
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # --- DYNAMIC MIGRATION LOGIC TO PREVENT BREAKING CHANGES ---
    ent_reg = er.async_get(hass)
    for description in SENSOR_TYPES + RESTORE_SENSOR_TYPES:
        old_unique_id = f"{coordinator.unique_id}_{DOMAIN}_{description.key}"
        new_unique_id = f"{entry.entry_id}_{DOMAIN}_{description.key}"
        if entity_id := ent_reg.async_get_entity_id("sensor", DOMAIN, old_unique_id):
            # Bulletproof check: Only migrate if the new ID isn't already taken!
            if not ent_reg.async_get_entity_id("sensor", DOMAIN, new_unique_id):
                try:
                    ent_reg.async_update_entity(entity_id, new_unique_id=new_unique_id)
                except ValueError:
                    pass
    # -----------------------------------------------------------

    sensors = []
    for description in SENSOR_TYPES:
        sensors.append(FlightRadar24Sensor(coordinator, description, entry.entry_id))
    for description in RESTORE_SENSOR_TYPES:
        sensors.append(FlightRadar24RestoreSensor(coordinator, description, entry.entry_id))
    async_add_entities(sensors, False)


class FlightRadar24Sensor(CoordinatorEntity[FlightRadar24Coordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: FlightRadar24SensorEntityDescription

    # TELL THE RECORDER TO IGNORE THE MASSIVE FLIGHTS ARRAY
    _unrecorded_attributes = frozenset({"flights"})

    def __init__(
            self,
            coordinator: FlightRadar24Coordinator,
            description: FlightRadar24SensorEntityDescription,
            entry_id: str,
    ) -> None:
        """Initialize."""
        self.entity_description = description
        super().__init__(coordinator)
        self._attr_device_info = coordinator.device_info
        self._attr_unique_id = f"{entry_id}_{DOMAIN}_{description.key}"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.entity_description.value(self.coordinator)
        if self.entity_description.attributes and self.entity_description.attributes(self.coordinator) is not None:
            new_attributes = copy.deepcopy(self.entity_description.attributes(self.coordinator))
            new_attributes["last_updated"] = datetime.datetime.now().isoformat()
            self._attr_extra_state_attributes = new_attributes
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.entity_description.value(self.coordinator) is not None


class FlightRadar24RestoreSensor(FlightRadar24Sensor, RestoreSensor):

    # WE MUST RECORD THESE SPECIFIC SENSORS TO RESTORE THEIR FLIGHT DATA ON REBOOT
    _unrecorded_attributes = frozenset()

    # Per-key restore handlers -- RESTORE_SENSOR_TYPES now backs more than one sensor
    # (additional_tracked, in_area) and each restores into a different FlightProcessor
    # slot via a different setter, so a single hardcoded set_tracked(...) call (the
    # original shape, when this class only backed additional_tracked) no longer fits.
    # Dispatching on entity_description.key keeps each restore call a plain one-liner
    # and makes it obvious where to add the next one, rather than branching inline in
    # async_added_to_hass() itself.
    def _restore_additional_tracked(self, flights: list[dict[str, Any]]) -> None:
        tracked = {}
        for flight in flights:
            tracked[flight.get('id') or flight.get('flight_number') or flight.get('callsign')] = flight
        self.coordinator.flight.set_tracked(tracked)

    def _restore_in_area(self, flights: list[dict[str, Any]]) -> None:
        # Same keying convention as _restore_additional_tracked above. set_in_area()
        # (see flight.py) also seeds _last_nonempty_monotonic to "now" and clears
        # _area_stale, so the just-restored data gets a full AREA_STALE_GRACE_S grace
        # window starting at restart, instead of zero protection against the very
        # first post-restart poll if it happens to be soft-blocked.
        in_area = {}
        for flight in flights:
            in_area[flight.get('id') or flight.get('flight_number') or flight.get('callsign')] = flight
        self.coordinator.flight.set_in_area(in_area)

    _RESTORE_HANDLERS = {
        "additional_tracked": _restore_additional_tracked,
        "in_area": _restore_in_area,
    }

    async def async_added_to_hass(self):
        """Restore state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()

        if not last_state:
            return

        handler = self._RESTORE_HANDLERS.get(self.entity_description.key)
        if handler is not None:
            handler(self, last_state.attributes.get('flights', {}))
