"""Select entities for Smart Energy Manager."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, OPERATING_MODES, MODE_AUTO, NO_CAR_SELECTED
from .coordinator import SmartEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = [OperatingModeSelect(coordinator, entry)]

    # En bilvals-select per laddare
    for ch_data in coordinator._get_charger_configs():
        charger_name = ch_data.get("name", "Laddare")
        entities.append(ActiveCarSelect(coordinator, entry, charger_name))

    async_add_entities(entities)


class _BaseSEMSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True

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


class OperatingModeSelect(_BaseSEMSelect):
    """Välj driftläge."""
    _attr_unique_id = "sem_operating_mode_select"
    _attr_name = "Operating Mode"
    _attr_icon = "mdi:tune"
    _attr_options = OPERATING_MODES

    @property
    def current_option(self) -> str:
        return self.coordinator.operating_mode

    async def async_select_option(self, option: str) -> None:
        self.coordinator.operating_mode = option
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()


class ActiveCarSelect(_BaseSEMSelect):
    """Välj vilken bil som är inkopplad på en specifik laddare."""
    _attr_icon = "mdi:car-electric"

    def __init__(
        self,
        coordinator: SmartEnergyCoordinator,
        entry: ConfigEntry,
        charger_name: str,
    ):
        super().__init__(coordinator, entry)
        self._charger_name = charger_name
        safe_name = charger_name.lower().replace(" ", "_")
        self._attr_unique_id = f"sem_charger_{safe_name}_active_car"
        self._attr_name = f"{charger_name} – Active Car"

    @property
    def options(self) -> list[str]:
        return self.coordinator.get_charger_car_options(self._charger_name)

    @property
    def current_option(self) -> str:
        return self.coordinator.get_active_car(self._charger_name)

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_active_car(self._charger_name, option)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
