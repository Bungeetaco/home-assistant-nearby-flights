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
)

RESTORE_SENSOR_TYPES: tuple[FlightRadar24SensorEntityDescription, ...] = (
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

    # "flights" is excluded from recorder history here for the same reason the plain
    # FlightRadar24Sensor already excludes it (large payload, not meaningful in a
    # history graph) -- restoring on reboot does NOT depend on this. RestoreEntity's
    # async_get_last_state() reads from homeassistant.helpers.restore_state's own
    # independent snapshot (.storage/core.restore_state, periodically dumped straight
    # from the live state machine), never from the recorder database, so it's
    # completely decoupled from _unrecorded_attributes / the recorder's
    # MAX_STATE_ATTRS_BYTES cutoff -- that setting only governs the SQL
    # history/logbook tables.
    _unrecorded_attributes = frozenset({"flights"})

    def _restore_in_area(self, flights: list[dict[str, Any]]) -> None:
        # set_in_area() (see api/flight.py) also seeds _last_nonempty_monotonic to
        # "now" and clears _area_stale, so the just-restored data gets a full
        # AREA_STALE_GRACE_S grace window starting at restart, instead of zero
        # protection against the very first post-restart poll if it happens to be
        # soft-blocked.
        in_area = {}
        for flight in flights:
            in_area[flight.get('id') or flight.get('flight_number') or flight.get('callsign')] = flight
        self.coordinator.flight.set_in_area(in_area)

    _RESTORE_HANDLERS = {
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
