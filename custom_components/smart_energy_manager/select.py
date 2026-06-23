"""Select entity for operating mode."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, OPERATING_MODES, MODE_AUTO
from .coordinator import SmartEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OperatingModeSelect(coordinator, entry)])


class OperatingModeSelect(CoordinatorEntity, SelectEntity):
    """Select the operating mode of the energy manager."""
    _attr_has_entity_name = True
    _attr_unique_id = "sem_operating_mode_select"
    _attr_name = "Operating Mode"
    _attr_icon = "mdi:tune"
    _attr_options = OPERATING_MODES

    def __init__(self, coordinator: SmartEnergyCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Smart Energy Manager",
            "manufacturer": "Custom",
            "model": "Smart Energy Manager",
        }

    @property
    def current_option(self) -> str:
        return self.coordinator.operating_mode

    async def async_select_option(self, option: str) -> None:
        self.coordinator.operating_mode = option
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
