"""Number entities for runtime-tunable parameters."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC,
    CONF_EXPORT_SELL_PERCENTILE, DEFAULT_EXPORT_SELL_PERCENTILE,
    CONF_EXPORT_MIN_SELL_PRICE_SEK_KWH, DEFAULT_EXPORT_MIN_SELL_PRICE_SEK_KWH,
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
        ExportSellPercentileNumber(coordinator, entry),
        ExportMinSellPriceNumber(coordinator, entry),
    ])


class _BaseSEMNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _config_key: str = ""  # entry.options-nyckel att skriva tillbaka till; "" = ingen persistens

    def __init__(self, coordinator: SmartEnergyCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry
        self._config: dict = {**entry.data, **entry.options}
        self._value: float = self._attr_native_min_value

    async def async_added_to_hass(self) -> None:
        """Återställ senaste värdet – skydd tills entry.options hunnit läsas om vid reload."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            try:
                self._value = float(last_state.state)
                self._update_controller()
            except (ValueError, TypeError):
                pass

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
        self._persist_to_options()
        self.async_write_ha_state()

    def _update_controller(self) -> None:
        pass

    def _config_value(self):
        """Värdet som ska sparas i entry.options – override vid enhetskonvertering."""
        return self._value

    def _persist_to_options(self) -> None:
        """Skriv tillbaka till entry.options så värdet överlever en omkonfiguration/omstart."""
        if not self._config_key:
            return
        new_options = {**self._entry.options, self._config_key: self._config_value()}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)


class BatteryMinSocNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_battery_min_soc"
    _attr_translation_key = "battery_min_soc"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 5.0
    _attr_native_max_value = 50.0
    _attr_native_step = 1.0
    _attr_icon = "mdi:battery-low"
    _config_key = CONF_BATTERY_MIN_SOC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC))

    def _update_controller(self):
        self.coordinator._controller.battery_min_soc = self._value


class BatteryMaxSocNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_battery_max_soc"
    _attr_translation_key = "battery_max_soc"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 50.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_icon = "mdi:battery-high"
    _config_key = CONF_BATTERY_MAX_SOC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC))

    def _update_controller(self):
        self.coordinator._controller.battery_max_soc = self._value


class ExportSellPercentileNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_export_sell_percentile"
    _attr_translation_key = "export_sell_percentile"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 50.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0
    _attr_icon = "mdi:chart-bar"
    _config_key = CONF_EXPORT_SELL_PERCENTILE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        raw = float(self._config.get(CONF_EXPORT_SELL_PERCENTILE, DEFAULT_EXPORT_SELL_PERCENTILE))
        self._value = round(raw * 100.0)

    def _update_controller(self):
        # Värdet hör hemma i EnergyPlanner (bygger DayPlan.export), inte EnergyController.
        self.coordinator._energy_planner.export_sell_percentile = self._value / 100.0

    def _config_value(self) -> float:
        return self._value / 100.0


class ExportMinSellPriceNumber(_BaseSEMNumber):
    _attr_unique_id = "sem_export_min_sell_price"
    _attr_translation_key = "export_min_sell_price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 3.0
    _attr_native_step = 0.05
    _attr_icon = "mdi:currency-usd"
    _config_key = CONF_EXPORT_MIN_SELL_PRICE_SEK_KWH

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._value = float(self._config.get(CONF_EXPORT_MIN_SELL_PRICE_SEK_KWH, DEFAULT_EXPORT_MIN_SELL_PRICE_SEK_KWH))

    def _update_controller(self):
        # Värdet hör hemma i EnergyPlanner men konsumeras inte av build_plan() –
        # marginalvärdesmodellen (etapp 2) styr export via battery_avg_cost +
        # cycle_cost istället för ett separat absolut minimipris.
        self.coordinator._energy_planner.export_min_sell_price = self._value
