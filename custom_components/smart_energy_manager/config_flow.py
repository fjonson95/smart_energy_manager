"""Config flow for Smart Energy Manager."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_BATTERY_INVERTER_POWER, CONF_BATTERY_INVERTER_CHARGE, CONF_BATTERY_INVERTER_DISCHARGE,
    CONF_BATTERY_SOC, CONF_BATTERY_CAPACITY_KWH, CONF_BATTERY_MAX_POWER_KW,
    CONF_SOLAR_INVERTER_TOTAL,
    CONF_SOLAR_INVERTER_POWER_L1, CONF_SOLAR_INVERTER_POWER_L2, CONF_SOLAR_INVERTER_POWER_L3,
    CONF_EV_CHARGER_POWER, CONF_EV_CHARGER_SWITCH, CONF_EV_CHARGER_CURRENT,
    CONF_EV_CHARGER_PHASE, CONF_EV_SOC, CONF_EV_SOC_TARGET,
    CONF_HEAT_PUMP_POWER, CONF_HEAT_PUMP_SWITCH, CONF_HEAT_PUMP_EXTRA_HOT_WATER,
    CONF_GRID_POWER_L1, CONF_GRID_POWER_L2, CONF_GRID_POWER_L3,
    CONF_GRID_CURRENT_L1, CONF_GRID_CURRENT_L2, CONF_GRID_CURRENT_L3,
    CONF_NORDPOOL_ENTITY, CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW,
    CONF_GRID_FEES, CONF_ENERGY_TAX, CONF_VAT_RATE, CONF_SELL_EXTRA_REVENUE,
    CONF_MAX_CURRENT_PER_PHASE, CONF_GRID_VOLTAGE,
    CONF_WINTER_MODE_ENABLED, CONF_WINTER_CHEAP_HOUR_THRESHOLD,
    CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD, CONF_WINTER_MIN_SOC, CONF_WINTER_MAX_SOC,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC,
    DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE, DEFAULT_VAT_RATE,
    DEFAULT_GRID_FEES, DEFAULT_ENERGY_TAX, DEFAULT_SELL_EXTRA_REVENUE,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC,
    DEFAULT_WINTER_MIN_SOC, DEFAULT_WINTER_MAX_SOC, DEFAULT_EV_SOC_TARGET,
    DEFAULT_WINTER_CHEAP_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD,
    EV_PHASES_OPTIONS,
)

ENTITY_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig())
NUMBER_SELECTOR = selector.NumberSelector(selector.NumberSelectorConfig(mode=selector.NumberSelectorMode.BOX))


def _entity_schema(optional_keys=None):
    """Build entity selectors."""
    return {
        vol.Required(CONF_BATTERY_SOC): ENTITY_SELECTOR,
        vol.Required(CONF_BATTERY_INVERTER_CHARGE): ENTITY_SELECTOR,
        vol.Required(CONF_BATTERY_INVERTER_DISCHARGE): ENTITY_SELECTOR,
        vol.Optional(CONF_BATTERY_INVERTER_POWER): ENTITY_SELECTOR,
        vol.Required(CONF_BATTERY_CAPACITY_KWH, default=10.0): NUMBER_SELECTOR,
        vol.Required(CONF_BATTERY_MAX_POWER_KW, default=5.0): NUMBER_SELECTOR,
        vol.Required(CONF_SOLAR_INVERTER_TOTAL): ENTITY_SELECTOR,
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L1): ENTITY_SELECTOR,
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L2): ENTITY_SELECTOR,
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L3): ENTITY_SELECTOR,
        vol.Required(CONF_EV_CHARGER_SWITCH): ENTITY_SELECTOR,
        vol.Required(CONF_EV_CHARGER_CURRENT): ENTITY_SELECTOR,
        vol.Optional(CONF_EV_CHARGER_POWER): ENTITY_SELECTOR,
        vol.Optional(CONF_EV_SOC): ENTITY_SELECTOR,
        vol.Required(CONF_EV_CHARGER_PHASE, default=EV_PHASES_OPTIONS[0]): selector.SelectSelector(
            selector.SelectSelectorConfig(options=EV_PHASES_OPTIONS)
        ),
        vol.Optional(CONF_HEAT_PUMP_SWITCH): ENTITY_SELECTOR,
        vol.Optional(CONF_HEAT_PUMP_POWER): ENTITY_SELECTOR,
        vol.Optional(CONF_HEAT_PUMP_EXTRA_HOT_WATER): ENTITY_SELECTOR,
        vol.Optional(CONF_GRID_POWER_L1): ENTITY_SELECTOR,
        vol.Optional(CONF_GRID_POWER_L2): ENTITY_SELECTOR,
        vol.Optional(CONF_GRID_POWER_L3): ENTITY_SELECTOR,
        vol.Optional(CONF_GRID_CURRENT_L1): ENTITY_SELECTOR,
        vol.Optional(CONF_GRID_CURRENT_L2): ENTITY_SELECTOR,
        vol.Optional(CONF_GRID_CURRENT_L3): ENTITY_SELECTOR,
        vol.Required(CONF_NORDPOOL_ENTITY): ENTITY_SELECTOR,
        vol.Required(CONF_SOLCAST_TODAY): ENTITY_SELECTOR,
        vol.Required(CONF_SOLCAST_TOMORROW): ENTITY_SELECTOR,
    }


class SmartEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Energy Manager."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step - entities."""
        errors = {}
        if user_input is not None:
            return await self.async_step_settings(user_input)

        schema = vol.Schema(_entity_schema())
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_settings(self, prev_input, user_input=None):
        """Step 2: grid and pricing settings."""
        if user_input is not None:
            data = {**prev_input, **user_input}
            return self.async_create_entry(title="Smart Energy Manager", data=data)

        schema = vol.Schema({
            vol.Required(CONF_MAX_CURRENT_PER_PHASE, default=DEFAULT_MAX_CURRENT): NUMBER_SELECTOR,
            vol.Required(CONF_GRID_VOLTAGE, default=DEFAULT_GRID_VOLTAGE): NUMBER_SELECTOR,
            vol.Required(CONF_GRID_FEES, default=DEFAULT_GRID_FEES): NUMBER_SELECTOR,
            vol.Required(CONF_ENERGY_TAX, default=DEFAULT_ENERGY_TAX): NUMBER_SELECTOR,
            vol.Required(CONF_VAT_RATE, default=DEFAULT_VAT_RATE): NUMBER_SELECTOR,
            vol.Required(CONF_SELL_EXTRA_REVENUE, default=DEFAULT_SELL_EXTRA_REVENUE): NUMBER_SELECTOR,
            vol.Required(CONF_BATTERY_MIN_SOC, default=DEFAULT_BATTERY_MIN_SOC): NUMBER_SELECTOR,
            vol.Required(CONF_BATTERY_MAX_SOC, default=DEFAULT_BATTERY_MAX_SOC): NUMBER_SELECTOR,
            vol.Required(CONF_EV_SOC_TARGET, default=DEFAULT_EV_SOC_TARGET): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_MODE_ENABLED, default=False): selector.BooleanSelector(),
            vol.Required(CONF_WINTER_CHEAP_HOUR_THRESHOLD, default=DEFAULT_WINTER_CHEAP_THRESHOLD): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD, default=DEFAULT_WINTER_EXPENSIVE_THRESHOLD): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_MIN_SOC, default=DEFAULT_WINTER_MIN_SOC): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_MAX_SOC, default=DEFAULT_WINTER_MAX_SOC): NUMBER_SELECTOR,
        })
        return self.async_show_form(step_id="settings", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return options flow."""
        return SmartEnergyOptionsFlow(config_entry)


class SmartEnergyOptionsFlow(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Required(CONF_MAX_CURRENT_PER_PHASE, default=data.get(CONF_MAX_CURRENT_PER_PHASE, DEFAULT_MAX_CURRENT)): NUMBER_SELECTOR,
            vol.Required(CONF_GRID_FEES, default=data.get(CONF_GRID_FEES, DEFAULT_GRID_FEES)): NUMBER_SELECTOR,
            vol.Required(CONF_ENERGY_TAX, default=data.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX)): NUMBER_SELECTOR,
            vol.Required(CONF_VAT_RATE, default=data.get(CONF_VAT_RATE, DEFAULT_VAT_RATE)): NUMBER_SELECTOR,
            vol.Required(CONF_SELL_EXTRA_REVENUE, default=data.get(CONF_SELL_EXTRA_REVENUE, DEFAULT_SELL_EXTRA_REVENUE)): NUMBER_SELECTOR,
            vol.Required(CONF_BATTERY_MIN_SOC, default=data.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)): NUMBER_SELECTOR,
            vol.Required(CONF_BATTERY_MAX_SOC, default=data.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC)): NUMBER_SELECTOR,
            vol.Required(CONF_EV_SOC_TARGET, default=data.get(CONF_EV_SOC_TARGET, DEFAULT_EV_SOC_TARGET)): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_MODE_ENABLED, default=data.get(CONF_WINTER_MODE_ENABLED, False)): selector.BooleanSelector(),
            vol.Required(CONF_WINTER_CHEAP_HOUR_THRESHOLD, default=data.get(CONF_WINTER_CHEAP_HOUR_THRESHOLD, DEFAULT_WINTER_CHEAP_THRESHOLD)): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD, default=data.get(CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD)): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_MIN_SOC, default=data.get(CONF_WINTER_MIN_SOC, DEFAULT_WINTER_MIN_SOC)): NUMBER_SELECTOR,
            vol.Required(CONF_WINTER_MAX_SOC, default=data.get(CONF_WINTER_MAX_SOC, DEFAULT_WINTER_MAX_SOC)): NUMBER_SELECTOR,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
