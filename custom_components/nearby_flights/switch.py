from __future__ import annotations

from typing import Any

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NearbyFlightsCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [NearbyFlightsScanEntity(coordinator, entry.entry_id)],
        False,
    )


class NearbyFlightsScanEntity(
    CoordinatorEntity[NearbyFlightsCoordinator],
    SwitchEntity,
    RestoreEntity,
):
    _attr_has_entity_name = True
    entity_description: SwitchEntityDescription

    def __init__(
        self,
        coordinator: NearbyFlightsCoordinator,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)

        self._attr_device_info = coordinator.device_info

        self.entity_description = SwitchEntityDescription(
            key="scanning",
            translation_key="scanning",
            icon="mdi:connection",
            entity_category=EntityCategory.CONFIG,
        )

        # FIXED: Lock down the unique ID using the entry_id
        self._attr_unique_id = (
            f"{entry_id}_{DOMAIN}_{self.entity_description.key}"
        )

    async def async_added_to_hass(self) -> None:
        """Restore the user's scanning on/off choice across restarts.

        Without this, every restart/reload silently re-enabled metered
        OpenSky polling even when the user had deliberately switched it off.
        """
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "off":
            self.coordinator.scanning = False
            self.coordinator.flight.clear_live_data()

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self.coordinator.scanning

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self.coordinator.scanning = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        self.coordinator.scanning = False

        self.coordinator.flight.clear_live_data()

        self.coordinator.async_set_updated_data(None)

        self.async_write_ha_state()
