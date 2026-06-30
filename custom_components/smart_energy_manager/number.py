"""Number entities for runtime-tunable parameters."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC, DEFAULT_EV_SOC_TARGET,
    DEFAULT_WINTER_CHEAP_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD,
    DEFAULT_WINTER_MIN_SOC, DEFAULT_WINTER_MAX_SOC,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC, CONF_EV_SOC_TARGET,
    CONF_WINTER_CHEAP_HOUR_THRESHOLD, CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD,
    CONF_WINTER_MIN_SOC, CONF_WINTER_MAX_SOC,
)
from .coordinator import SmartEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        BatteryMinSocNumber(coordinator, entry),
        BatteryMaxSocNumber(coordinator, entry),
        EvSocTargetNumber(coordinator, entry),
        WinterCheapThresholdNumber(coordinator, entry),
        WinterExpensiveThresholdNumber(coordinator, entry),
        WinterMinSocNumber(coordinator, entry),
        WinterMaxSocNumber(coordinator, entry),
    ])


class _BaseSEMNumber(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SmartEnergyCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry
        self._config: dict = {**entry.data, **entry.options}
        self._value: float = self._attr_native_min_value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Smart Energy Manager",
            "manufacturer": "Custom",
            "model": "Smart Energy Manager",
        }

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self._update_controller()
        self.async_write_ha_state()

    def _update_controller(self) -> None:
        pass


class BatteryMinSocNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_battery_min_soc"
    _attr_name = "Battery Min SOC"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 5.0
    _attr_native_max_value = 50.0
    _attr_native_step = 1.0
    _attr_icon = "mdi:battery-low"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC))

    def _update_controller(self):
        self.coordinator._controller.battery_min_soc = self._value


class BatteryMaxSocNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_battery_max_soc"
    _attr_name = "Battery Max SOC"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 50.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_icon = "mdi:battery-high"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC))

    def _update_controller(self):
        self.coordinator._controller.battery_max_soc = self._value


class EvSocTargetNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_ev_soc_target"
    _attr_name = "EV SOC Target"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 20.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0
    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_EV_SOC_TARGET, DEFAULT_EV_SOC_TARGET))

    def _update_controller(self):
        self.coordinator._controller.ev_soc_target = self._value


class WinterCheapThresholdNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_winter_cheap_threshold"
    _attr_name = "Winter Cheap Price Threshold"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 3.0
    _attr_native_step = 0.05
    _attr_icon = "mdi:currency-usd"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_WINTER_CHEAP_HOUR_THRESHOLD, DEFAULT_WINTER_CHEAP_THRESHOLD))

    def _update_controller(self):
        self.coordinator._controller.winter_cheap_threshold = self._value


class WinterExpensiveThresholdNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_winter_expensive_threshold"
    _attr_name = "Winter Expensive Price Threshold"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_native_min_value = 0.5
    _attr_native_max_value = 5.0
    _attr_native_step = 0.05
    _attr_icon = "mdi:currency-usd"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD))

    def _update_controller(self):
        self.coordinator._controller.winter_expensive_threshold = self._value


class WinterMinSocNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_winter_min_soc"
    _attr_name = "Winter Min SOC"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 10.0
    _attr_native_max_value = 80.0
    _attr_native_step = 5.0
    _attr_icon = "mdi:snowflake"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_WINTER_MIN_SOC, DEFAULT_WINTER_MIN_SOC))

    def _update_controller(self):
        self.coordinator._controller.winter_min_soc = self._value


class WinterMaxSocNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_winter_max_soc"
    _attr_name = "Winter Max SOC"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 50.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0
    _attr_icon = "mdi:snowflake"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_WINTER_MAX_SOC, DEFAULT_WINTER_MAX_SOC))

    def _update_controller(self):
        self.coordinator._controller.winter_max_soc = self._value
