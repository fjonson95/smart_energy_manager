"""Config flow for Smart Energy Manager."""
from __future__ import annotations

import logging
from typing import Any

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
    CONF_EV_CARS,
    CONF_HEAT_PUMP_POWER, CONF_HEAT_PUMP_SWITCH, CONF_HEAT_PUMP_EXTRA_HOT_WATER,
    CONF_HEAT_PUMP_PHASE, CONF_HEAT_PUMP_PATRON_PHASES, CONF_HEAT_PUMP_PATRON_POWER_KW,
    CONF_GRID_POWER_L1, CONF_GRID_POWER_L2, CONF_GRID_POWER_L3,
    CONF_GRID_CURRENT_L1, CONF_GRID_CURRENT_L2, CONF_GRID_CURRENT_L3,
    CONF_NORDPOOL_ENTITY, CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW,
    CONF_GRID_FEES, CONF_ENERGY_TAX, CONF_VAT_RATE, CONF_SELL_EXTRA_REVENUE,
    CONF_MAX_CURRENT_PER_PHASE, CONF_GRID_VOLTAGE,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC,
    CONF_WINTER_CHEAP_HOUR_THRESHOLD, CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD,
    CONF_WINTER_MIN_SOC, CONF_WINTER_MAX_SOC,
    DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE, DEFAULT_VAT_RATE,
    DEFAULT_GRID_FEES, DEFAULT_ENERGY_TAX, DEFAULT_SELL_EXTRA_REVENUE,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC,
    DEFAULT_WINTER_CHEAP_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD,
    DEFAULT_WINTER_MIN_SOC, DEFAULT_WINTER_MAX_SOC,
    DEFAULT_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PATRON_PHASES, DEFAULT_HEAT_PUMP_PATRON_POWER_KW,
    EV_PHASES_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

# ── Selector helpers ──────────────────────────────────────────────────────────

def _entity_selector(domain: str | list[str] | None = None) -> selector.EntitySelector:
    if domain:
        domains = domain if isinstance(domain, list) else [domain]
        return selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domains)
        )
    return selector.EntitySelector()


def _optional_entity(domain: str | list[str] | None = None):
    """Entity selector that also accepts empty string (= not configured)."""
    return vol.Any(
        vol.All(str, vol.Length(min=0, max=0)),  # empty string → not set
        _entity_selector(domain),
    )


# ── Step schemas ──────────────────────────────────────────────────────────────

STEP_GRID_PRICING_SCHEMA = vol.Schema({
    vol.Required(CONF_NORDPOOL_ENTITY): _entity_selector("sensor"),
    vol.Optional(CONF_GRID_POWER_L1): _entity_selector("sensor"),
    vol.Optional(CONF_GRID_POWER_L2): _entity_selector("sensor"),
    vol.Optional(CONF_GRID_POWER_L3): _entity_selector("sensor"),
    vol.Optional(CONF_GRID_CURRENT_L1): _entity_selector("sensor"),
    vol.Optional(CONF_GRID_CURRENT_L2): _entity_selector("sensor"),
    vol.Optional(CONF_GRID_CURRENT_L3): _entity_selector("sensor"),
    vol.Optional(CONF_MAX_CURRENT_PER_PHASE, default=DEFAULT_MAX_CURRENT): vol.Coerce(float),
    vol.Optional(CONF_GRID_VOLTAGE, default=DEFAULT_GRID_VOLTAGE): vol.Coerce(float),
    vol.Optional(CONF_GRID_FEES, default=DEFAULT_GRID_FEES): vol.Coerce(float),
    vol.Optional(CONF_ENERGY_TAX, default=DEFAULT_ENERGY_TAX): vol.Coerce(float),
    vol.Optional(CONF_VAT_RATE, default=DEFAULT_VAT_RATE): vol.Coerce(float),
    vol.Optional(CONF_SELL_EXTRA_REVENUE, default=DEFAULT_SELL_EXTRA_REVENUE): vol.Coerce(float),
})

STEP_SOLAR_SCHEMA = vol.Schema({
    vol.Optional(CONF_SOLAR_INVERTER_TOTAL): _entity_selector("sensor"),
    vol.Optional(CONF_SOLAR_INVERTER_POWER_L1): _entity_selector("sensor"),
    vol.Optional(CONF_SOLAR_INVERTER_POWER_L2): _entity_selector("sensor"),
    vol.Optional(CONF_SOLAR_INVERTER_POWER_L3): _entity_selector("sensor"),
    vol.Optional(CONF_SOLCAST_TODAY): _entity_selector("sensor"),
    vol.Optional(CONF_SOLCAST_TOMORROW): _entity_selector("sensor"),
})

STEP_BATTERY_SCHEMA = vol.Schema({
    vol.Optional(CONF_BATTERY_SOC): _entity_selector("sensor"),
    vol.Optional(CONF_BATTERY_INVERTER_POWER): _entity_selector("sensor"),
    vol.Optional(CONF_BATTERY_INVERTER_CHARGE): _entity_selector(["sensor", "number"]),
    vol.Optional(CONF_BATTERY_INVERTER_DISCHARGE): _entity_selector(["sensor", "number"]),
    vol.Optional(CONF_BATTERY_CAPACITY_KWH, default=10.0): vol.Coerce(float),
    vol.Optional(CONF_BATTERY_MAX_POWER_KW, default=5.0): vol.Coerce(float),
    vol.Optional(CONF_BATTERY_MIN_SOC, default=DEFAULT_BATTERY_MIN_SOC): vol.Coerce(float),
    vol.Optional(CONF_BATTERY_MAX_SOC, default=DEFAULT_BATTERY_MAX_SOC): vol.Coerce(float),
})

STEP_HEAT_PUMP_SCHEMA = vol.Schema({
    vol.Optional(CONF_HEAT_PUMP_POWER): _entity_selector("sensor"),
    vol.Optional(CONF_HEAT_PUMP_SWITCH): _entity_selector("switch"),
    vol.Optional(CONF_HEAT_PUMP_EXTRA_HOT_WATER): _entity_selector(["switch", "input_boolean"]),
    vol.Optional(CONF_HEAT_PUMP_PHASE, default=DEFAULT_HEAT_PUMP_PHASE): selector.SelectSelector(
        selector.SelectSelectorConfig(options=EV_PHASES_OPTIONS, mode=selector.SelectSelectorMode.LIST)
    ),
    vol.Optional(CONF_HEAT_PUMP_PATRON_POWER_KW, default=DEFAULT_HEAT_PUMP_PATRON_POWER_KW): vol.Coerce(float),
})

# EV car sub-flow (one car at a time)
STEP_EV_CAR_SCHEMA = vol.Schema({
    vol.Required("car_name"): str,
    vol.Required("charger_switch"): _entity_selector("switch"),
    vol.Required("charger_current"): _entity_selector(["number", "input_number"]),
    vol.Optional("charger_power"): _entity_selector("sensor"),
    vol.Optional("ev_soc"): _entity_selector("sensor"),
    vol.Optional("ev_soc_target", default=80.0): vol.Coerce(float),
    vol.Required("phases", default=1): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[{"label": "1-fas", "value": "1"}, {"label": "3-fas", "value": "3"}],
            mode=selector.SelectSelectorMode.LIST,
        )
    ),
    vol.Optional("phase", default="L1"): selector.SelectSelector(
        selector.SelectSelectorConfig(options=EV_PHASES_OPTIONS, mode=selector.SelectSelectorMode.LIST)
    ),
})


class SmartEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow for Smart Energy Manager."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._ev_cars: list[dict] = []

    # ── Step 1: Grid & Pricing ────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 – Grid & Pricing."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_NORDPOOL_ENTITY):
                errors[CONF_NORDPOOL_ENTITY] = "required"
            else:
                self._data.update(user_input)
                return await self.async_step_solar()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_GRID_PRICING_SCHEMA,
            errors=errors,
            description_placeholders={"title": "Nät & Prissättning"},
        )

    # ── Step 2: Solar ─────────────────────────────────────────────────────────

    async def async_step_solar(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 – Solar inverter."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_battery()

        return self.async_show_form(
            step_id="solar",
            data_schema=STEP_SOLAR_SCHEMA,
            description_placeholders={"title": "Solceller"},
        )

    # ── Step 3: Battery ───────────────────────────────────────────────────────

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3 – Battery storage."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_heat_pump()

        return self.async_show_form(
            step_id="battery",
            data_schema=STEP_BATTERY_SCHEMA,
            description_placeholders={"title": "Batteri"},
        )

    # ── Step 4: Heat pump ─────────────────────────────────────────────────────

    async def async_step_heat_pump(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4 – Heat pump / boiler."""
        if user_input is not None:
            # Store patron phases as a list; user picks the compressor phase (1-phase),
            # patron always uses the remaining two phases.
            comp_phase = user_input.get(CONF_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PHASE)
            patron_phases = [p for p in ["L1", "L2", "L3"] if p != comp_phase]
            user_input[CONF_HEAT_PUMP_PATRON_PHASES] = patron_phases
            self._data.update(user_input)
            return await self.async_step_ev_menu()

        return self.async_show_form(
            step_id="heat_pump",
            data_schema=STEP_HEAT_PUMP_SCHEMA,
            description_placeholders={"title": "Elpanna / Värmepump"},
        )

    # ── Step 5: EV menu (add car / done) ─────────────────────────────────────

    async def async_step_ev_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 5 – Decide whether to add an EV."""
        if user_input is not None:
            if user_input.get("add_car"):
                return await self.async_step_ev_car()
            else:
                return self._create_entry()

        added = len(self._ev_cars)
        schema = vol.Schema({
            vol.Required("add_car", default=added == 0): bool,
        })
        return self.async_show_form(
            step_id="ev_menu",
            data_schema=schema,
            description_placeholders={
                "count": str(added),
                "title": f"Elbil – {added} bil(ar) tillagda",
            },
        )

    # ── Step 5b: EV car details ───────────────────────────────────────────────

    async def async_step_ev_car(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 5b – Configure one EV car."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get("car_name"):
                errors["car_name"] = "required"
            elif not user_input.get("charger_switch"):
                errors["charger_switch"] = "required"
            elif not user_input.get("charger_current"):
                errors["charger_current"] = "required"
            else:
                car: dict[str, Any] = {
                    "name": user_input["car_name"],
                    "charger_switch": user_input["charger_switch"],
                    "charger_current": user_input["charger_current"],
                    "charger_power": user_input.get("charger_power") or None,
                    "ev_soc": user_input.get("ev_soc") or None,
                    "ev_soc_target": float(user_input.get("ev_soc_target", 80.0)),
                    "phases": int(user_input.get("phases", 1)),
                    "phase": user_input.get("phase", "L1") if int(user_input.get("phases", 1)) == 1 else None,
                }
                self._ev_cars.append(car)
                return await self.async_step_ev_menu()

        return self.async_show_form(
            step_id="ev_car",
            data_schema=STEP_EV_CAR_SCHEMA,
            errors=errors,
            description_placeholders={"title": f"Elbil {len(self._ev_cars) + 1}"},
        )

    # ── Finalise ──────────────────────────────────────────────────────────────

    def _create_entry(self) -> config_entries.FlowResult:
        self._data[CONF_EV_CARS] = self._ev_cars
        return self.async_create_entry(
            title="Smart Energy Manager",
            data=self._data,
        )

    # ── Options flow ──────────────────────────────────────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return SmartEnergyOptionsFlow(config_entry)


class SmartEnergyOptionsFlow(config_entries.OptionsFlow):
    """Options flow – edit pricing, battery limits and EV cars after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._data: dict[str, Any] = dict(config_entry.options or config_entry.data)
        self._ev_cars: list[dict] = list(self._data.get(CONF_EV_CARS, []))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Options root – Grid & Pricing."""
        errors: dict[str, str] = {}
        current = self._config_entry.data

        if user_input is not None:
            if not user_input.get(CONF_NORDPOOL_ENTITY):
                errors[CONF_NORDPOOL_ENTITY] = "required"
            else:
                self._data.update(user_input)
                return await self.async_step_battery_opts()

        defaults = {
            CONF_NORDPOOL_ENTITY: current.get(CONF_NORDPOOL_ENTITY, ""),
            CONF_GRID_POWER_L1: current.get(CONF_GRID_POWER_L1, ""),
            CONF_GRID_POWER_L2: current.get(CONF_GRID_POWER_L2, ""),
            CONF_GRID_POWER_L3: current.get(CONF_GRID_POWER_L3, ""),
            CONF_GRID_CURRENT_L1: current.get(CONF_GRID_CURRENT_L1, ""),
            CONF_GRID_CURRENT_L2: current.get(CONF_GRID_CURRENT_L2, ""),
            CONF_GRID_CURRENT_L3: current.get(CONF_GRID_CURRENT_L3, ""),
            CONF_MAX_CURRENT_PER_PHASE: current.get(CONF_MAX_CURRENT_PER_PHASE, DEFAULT_MAX_CURRENT),
            CONF_GRID_VOLTAGE: current.get(CONF_GRID_VOLTAGE, DEFAULT_GRID_VOLTAGE),
            CONF_GRID_FEES: current.get(CONF_GRID_FEES, DEFAULT_GRID_FEES),
            CONF_ENERGY_TAX: current.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX),
            CONF_VAT_RATE: current.get(CONF_VAT_RATE, DEFAULT_VAT_RATE),
            CONF_SELL_EXTRA_REVENUE: current.get(CONF_SELL_EXTRA_REVENUE, DEFAULT_SELL_EXTRA_REVENUE),
        }

        return self.async_show_form(
            step_id="init",
            data_schema=self._fill_defaults(STEP_GRID_PRICING_SCHEMA, defaults),
            errors=errors,
        )

    async def async_step_battery_opts(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Options – battery limits."""
        current = self._config_entry.data
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_ev_menu_opts()

        defaults = {
            CONF_BATTERY_MIN_SOC: current.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC),
            CONF_BATTERY_MAX_SOC: current.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC),
            CONF_BATTERY_CAPACITY_KWH: current.get(CONF_BATTERY_CAPACITY_KWH, 10.0),
            CONF_BATTERY_MAX_POWER_KW: current.get(CONF_BATTERY_MAX_POWER_KW, 5.0),
        }
        schema = vol.Schema({
            vol.Optional(CONF_BATTERY_MIN_SOC, default=defaults[CONF_BATTERY_MIN_SOC]): vol.Coerce(float),
            vol.Optional(CONF_BATTERY_MAX_SOC, default=defaults[CONF_BATTERY_MAX_SOC]): vol.Coerce(float),
            vol.Optional(CONF_BATTERY_CAPACITY_KWH, default=defaults[CONF_BATTERY_CAPACITY_KWH]): vol.Coerce(float),
            vol.Optional(CONF_BATTERY_MAX_POWER_KW, default=defaults[CONF_BATTERY_MAX_POWER_KW]): vol.Coerce(float),
        })
        return self.async_show_form(step_id="battery_opts", data_schema=schema)

    async def async_step_ev_menu_opts(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Options – manage EV cars."""
        if user_input is not None:
            if user_input.get("add_car"):
                return await self.async_step_ev_car_opts()
            else:
                self._data[CONF_EV_CARS] = self._ev_cars
                return self.async_create_entry(title="", data=self._data)

        added = len(self._ev_cars)
        schema = vol.Schema({vol.Required("add_car", default=False): bool})
        return self.async_show_form(
            step_id="ev_menu_opts",
            data_schema=schema,
            description_placeholders={"count": str(added)},
        )

    async def async_step_ev_car_opts(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Options – add/edit one EV car."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("car_name") or not user_input.get("charger_switch") or not user_input.get("charger_current"):
                errors["base"] = "required_fields"
            else:
                car: dict[str, Any] = {
                    "name": user_input["car_name"],
                    "charger_switch": user_input["charger_switch"],
                    "charger_current": user_input["charger_current"],
                    "charger_power": user_input.get("charger_power") or None,
                    "ev_soc": user_input.get("ev_soc") or None,
                    "ev_soc_target": float(user_input.get("ev_soc_target", 80.0)),
                    "phases": int(user_input.get("phases", 1)),
                    "phase": user_input.get("phase", "L1") if int(user_input.get("phases", 1)) == 1 else None,
                }
                self._ev_cars.append(car)
                return await self.async_step_ev_menu_opts()

        return self.async_show_form(
            step_id="ev_car_opts",
            data_schema=STEP_EV_CAR_SCHEMA,
            errors=errors,
        )

    @staticmethod
    def _fill_defaults(schema: vol.Schema, defaults: dict) -> vol.Schema:
        """Return schema with updated default values."""
        new_schema = {}
        for key, validator in schema.schema.items():
            key_str = key.schema if hasattr(key, "schema") else str(key)
            if key_str in defaults:
                if isinstance(key, vol.Required):
                    new_schema[vol.Required(key_str, default=defaults[key_str])] = validator
                else:
                    new_schema[vol.Optional(key_str, default=defaults[key_str])] = validator
            else:
                new_schema[key] = validator
        return vol.Schema(new_schema)
