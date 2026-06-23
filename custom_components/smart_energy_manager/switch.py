"""Switches for Smart Energy Manager."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AUTO, MODE_FORCE_CHARGE_EV, MODE_FORCE_CHARGE_BATTERY, MODE_WINTER
from .coordinator import SmartEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ForceEVChargeSwitch(coordinator, entry),
        WinterModeSwitch(coordinator, entry),
        ForceChargeBatterySwitch(coordinator, entry),
    ])


class _BaseSEMSwitch(CoordinatorEntity, SwitchEntity):
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


class ForceEVChargeSwitch(_BaseSEMSwitch):
    """Force EV charging from grid regardless of solar."""
    _attr_unique_id = "sem_force_ev_charge"
    _attr_name = "Force EV Charge from Grid"
    _attr_icon = "mdi:car-electric"

    @property
    def is_on(self) -> bool:
        return self.coordinator.operating_mode == MODE_FORCE_CHARGE_EV

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.operating_mode = MODE_FORCE_CHARGE_EV
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.operating_mode = MODE_AUTO
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()


class WinterModeSwitch(_BaseSEMSwitch):
    """Enable winter mode (charge cheap / discharge expensive)."""
    _attr_unique_id = "sem_winter_mode"
    _attr_name = "Winter Mode"
    _attr_icon = "mdi:snowflake"

    @property
    def is_on(self) -> bool:
        return self.coordinator.operating_mode == MODE_WINTER

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.operating_mode = MODE_WINTER
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.operating_mode = MODE_AUTO
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()


class ForceChargeBatterySwitch(_BaseSEMSwitch):
    """Force charge battery from grid."""
    _attr_unique_id = "sem_force_battery_charge"
    _attr_name = "Force Charge Battery from Grid"
    _attr_icon = "mdi:battery-charging"

    @property
    def is_on(self) -> bool:
        return self.coordinator.operating_mode == MODE_FORCE_CHARGE_BATTERY

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.operating_mode = MODE_FORCE_CHARGE_BATTERY
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.operating_mode = MODE_AUTO
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
