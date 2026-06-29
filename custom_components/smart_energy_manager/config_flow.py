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
    CONF_HOUSE_LOAD_ENTITY, CONF_GRID_POWER_UNIT, CONF_EV_POWER_UNIT, UNIT_W, UNIT_KW,
    CONF_LEGIONELLA_ENABLED, CONF_LEGIONELLA_INTERVAL_DAYS,
    CONF_LEGIONELLA_PREFERRED_HOUR_START, CONF_LEGIONELLA_PREFERRED_HOUR_END,
    CONF_LEGIONELLA_MAX_PRICE, CONF_LEGIONELLA_DURATION_MINUTES,
    DEFAULT_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_INTERVAL_DAYS,
    DEFAULT_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_END,
    DEFAULT_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_DURATION_MINUTES,
    EV_PHASES_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)


# ── Selector helpers ──────────────────────────────────────────────────────────

def _entity_selector(domain: str | list[str] | None = None) -> selector.EntitySelector:
    """Entity selector that requires a value (use for mandatory fields)."""
    if domain:
        domains = domain if isinstance(domain, list) else [domain]
        return selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domains)
        )
    return selector.EntitySelector()


def _opt_entity_selector(domain: str | list[str] | None = None):
    """
    Truly optional entity selector.

    HA's EntitySelector always requires a selection. We work around this by
    accepting either a valid entity_id string OR an empty string / None, and
    normalising empty/None to None in the step handler.  The UI widget shown
    is a plain text input so the user can simply leave it blank.
    """
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT))


def _d(data: dict, key: str, default=None):
    """Get value from dict with fallback."""
    return data.get(key, default)


# ── Step schemas ──────────────────────────────────────────────────────────────

def _grid_schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_NORDPOOL_ENTITY, default=_d(defaults, CONF_NORDPOOL_ENTITY, "")): _entity_selector("sensor"),
        vol.Optional(CONF_GRID_POWER_L1, default=_d(defaults, CONF_GRID_POWER_L1, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_POWER_L2, default=_d(defaults, CONF_GRID_POWER_L2, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_POWER_L3, default=_d(defaults, CONF_GRID_POWER_L3, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_CURRENT_L1, default=_d(defaults, CONF_GRID_CURRENT_L1, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_CURRENT_L2, default=_d(defaults, CONF_GRID_CURRENT_L2, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_CURRENT_L3, default=_d(defaults, CONF_GRID_CURRENT_L3, "")): _opt_entity_selector(),
        vol.Optional(CONF_MAX_CURRENT_PER_PHASE, default=_d(defaults, CONF_MAX_CURRENT_PER_PHASE, DEFAULT_MAX_CURRENT)): vol.Coerce(float),
        vol.Optional(CONF_GRID_VOLTAGE, default=_d(defaults, CONF_GRID_VOLTAGE, DEFAULT_GRID_VOLTAGE)): vol.Coerce(float),
        vol.Optional(CONF_GRID_FEES, default=_d(defaults, CONF_GRID_FEES, DEFAULT_GRID_FEES)): vol.Coerce(float),
        vol.Optional(CONF_ENERGY_TAX, default=_d(defaults, CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX)): vol.Coerce(float),
        vol.Optional(CONF_VAT_RATE, default=_d(defaults, CONF_VAT_RATE, DEFAULT_VAT_RATE)): vol.Coerce(float),
        vol.Optional(CONF_SELL_EXTRA_REVENUE, default=_d(defaults, CONF_SELL_EXTRA_REVENUE, DEFAULT_SELL_EXTRA_REVENUE)): vol.Coerce(float),
        vol.Optional(CONF_HOUSE_LOAD_ENTITY, default=_d(defaults, CONF_HOUSE_LOAD_ENTITY, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_POWER_UNIT, default=_d(defaults, CONF_GRID_POWER_UNIT, UNIT_W)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[{"label": "Watt (W)", "value": "W"}, {"label": "Kilowatt (kW)", "value": "kW"}],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_EV_POWER_UNIT, default=_d(defaults, CONF_EV_POWER_UNIT, UNIT_W)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[{"label": "Watt (W)", "value": "W"}, {"label": "Kilowatt (kW)", "value": "kW"}],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    })


def _solar_schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_SOLAR_INVERTER_TOTAL, default=_d(defaults, CONF_SOLAR_INVERTER_TOTAL, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L1, default=_d(defaults, CONF_SOLAR_INVERTER_POWER_L1, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L2, default=_d(defaults, CONF_SOLAR_INVERTER_POWER_L2, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L3, default=_d(defaults, CONF_SOLAR_INVERTER_POWER_L3, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLCAST_TODAY, default=_d(defaults, CONF_SOLCAST_TODAY, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLCAST_TOMORROW, default=_d(defaults, CONF_SOLCAST_TOMORROW, "")): _opt_entity_selector(),
    })


def _battery_schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_BATTERY_SOC, default=_d(defaults, CONF_BATTERY_SOC, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_INVERTER_POWER, default=_d(defaults, CONF_BATTERY_INVERTER_POWER, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_INVERTER_CHARGE, default=_d(defaults, CONF_BATTERY_INVERTER_CHARGE, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_INVERTER_DISCHARGE, default=_d(defaults, CONF_BATTERY_INVERTER_DISCHARGE, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_CAPACITY_KWH, default=_d(defaults, CONF_BATTERY_CAPACITY_KWH, 10.0)): vol.Coerce(float),
        vol.Optional(CONF_BATTERY_MAX_POWER_KW, default=_d(defaults, CONF_BATTERY_MAX_POWER_KW, 5.0)): vol.Coerce(float),
        vol.Optional(CONF_BATTERY_MIN_SOC, default=_d(defaults, CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)): vol.Coerce(float),
        vol.Optional(CONF_BATTERY_MAX_SOC, default=_d(defaults, CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC)): vol.Coerce(float),
    })


def _heat_pump_schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_HEAT_PUMP_POWER, default=_d(defaults, CONF_HEAT_PUMP_POWER, "")): _opt_entity_selector(),
        vol.Optional(CONF_HEAT_PUMP_SWITCH, default=_d(defaults, CONF_HEAT_PUMP_SWITCH, "")): _opt_entity_selector(),
        vol.Optional(CONF_HEAT_PUMP_EXTRA_HOT_WATER, default=_d(defaults, CONF_HEAT_PUMP_EXTRA_HOT_WATER, "")): _opt_entity_selector(),
        vol.Optional(CONF_HEAT_PUMP_PHASE, default=_d(defaults, CONF_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PHASE)): selector.SelectSelector(
            selector.SelectSelectorConfig(options=EV_PHASES_OPTIONS, mode=selector.SelectSelectorMode.LIST)
        ),
        vol.Optional(CONF_HEAT_PUMP_PATRON_POWER_KW, default=_d(defaults, CONF_HEAT_PUMP_PATRON_POWER_KW, DEFAULT_HEAT_PUMP_PATRON_POWER_KW)): vol.Coerce(float),
    })


def _legionella_schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_LEGIONELLA_ENABLED, default=_d(defaults, CONF_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_ENABLED)): bool,
        vol.Optional(CONF_LEGIONELLA_INTERVAL_DAYS, default=_d(defaults, CONF_LEGIONELLA_INTERVAL_DAYS, DEFAULT_LEGIONELLA_INTERVAL_DAYS)): vol.Coerce(int),
        vol.Optional(CONF_LEGIONELLA_PREFERRED_HOUR_START, default=_d(defaults, CONF_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_START)): vol.Coerce(int),
        vol.Optional(CONF_LEGIONELLA_PREFERRED_HOUR_END, default=_d(defaults, CONF_LEGIONELLA_PREFERRED_HOUR_END, DEFAULT_LEGIONELLA_PREFERRED_HOUR_END)): vol.Coerce(int),
        vol.Optional(CONF_LEGIONELLA_MAX_PRICE, default=_d(defaults, CONF_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_MAX_PRICE)): vol.Coerce(float),
        vol.Optional(CONF_LEGIONELLA_DURATION_MINUTES, default=_d(defaults, CONF_LEGIONELLA_DURATION_MINUTES, DEFAULT_LEGIONELLA_DURATION_MINUTES)): vol.Coerce(int),
    })


def _ev_car_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema({
        vol.Required("car_name", default=_d(d, "name", "")): str,
        vol.Required("charger_switch", default=_d(d, "charger_switch", "")): _opt_entity_selector(),
        vol.Required("charger_current", default=_d(d, "charger_current", "")): _opt_entity_selector(),
        vol.Optional("charger_power", default=_d(d, "charger_power", "")): _opt_entity_selector(),
        vol.Optional("ev_soc", default=_d(d, "ev_soc", "")): _opt_entity_selector(),
        vol.Optional("ev_soc_target", default=_d(d, "ev_soc_target", 80.0)): vol.Coerce(float),
        vol.Required("phases", default=str(_d(d, "phases", 1))): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[{"label": "1-fas", "value": "1"}, {"label": "3-fas", "value": "3"}],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Optional("phase", default=_d(d, "phase", "L1") or "L1"): selector.SelectSelector(
            selector.SelectSelectorConfig(options=EV_PHASES_OPTIONS, mode=selector.SelectSelectorMode.LIST)
        ),
    })


def _car_dict_from_input(user_input: dict) -> dict:
    phases = int(user_input.get("phases", 1))
    return {
        "name": user_input["car_name"],
        "charger_switch": user_input["charger_switch"],
        "charger_current": user_input["charger_current"],
        "charger_power": user_input.get("charger_power") or None,
        "ev_soc": user_input.get("ev_soc") or None,
        "ev_soc_target": float(user_input.get("ev_soc_target", 80.0)),
        "phases": phases,
        "phase": user_input.get("phase", "L1") if phases == 1 else None,
    }


def _normalise(data: dict) -> dict:
    """Convert empty strings to None for all entity-id fields."""
    result = {}
    for k, v in data.items():
        if isinstance(v, str) and v.strip() == "":
            result[k] = None
        else:
            result[k] = v
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG FLOW
# ══════════════════════════════════════════════════════════════════════════════

class SmartEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow for Smart Energy Manager."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._ev_cars: list[dict] = []

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_NORDPOOL_ENTITY):
                errors[CONF_NORDPOOL_ENTITY] = "required"
            else:
                self._data.update(_normalise(user_input))
                return await self.async_step_solar()
        return self.async_show_form(
            step_id="user",
            data_schema=_grid_schema({}),
            errors=errors,
        )

    async def async_step_solar(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_battery()
        return self.async_show_form(step_id="solar", data_schema=_solar_schema({}))

    async def async_step_battery(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_heat_pump()
        return self.async_show_form(step_id="battery", data_schema=_battery_schema({}))

    async def async_step_heat_pump(self, user_input=None):
        if user_input is not None:
            data = _normalise(user_input)
            comp_phase = data.get(CONF_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PHASE)
            data[CONF_HEAT_PUMP_PATRON_PHASES] = [p for p in ["L1", "L2", "L3"] if p != comp_phase]
            self._data.update(data)
            return await self.async_step_legionella()
        return self.async_show_form(step_id="heat_pump", data_schema=_heat_pump_schema({}))

    async def async_step_legionella(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_ev_menu()
        return self.async_show_form(step_id="legionella", data_schema=_legionella_schema({}))

    async def async_step_ev_menu(self, user_input=None):
        if user_input is not None:
            if user_input.get("add_car"):
                return await self.async_step_ev_car()
            else:
                return self._create_entry()
        added = len(self._ev_cars)
        return self.async_show_form(
            step_id="ev_menu",
            data_schema=vol.Schema({vol.Required("add_car", default=added == 0): bool}),
            description_placeholders={"count": str(added)},
        )

    async def async_step_ev_car(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get("car_name") or not user_input.get("charger_switch") or not user_input.get("charger_current"):
                errors["base"] = "required_fields"
            else:
                self._ev_cars.append(_car_dict_from_input(user_input))
                return await self.async_step_ev_menu()
        return self.async_show_form(
            step_id="ev_car",
            data_schema=_ev_car_schema(),
            errors=errors,
            description_placeholders={"title": f"Elbil {len(self._ev_cars) + 1}"},
        )

    def _create_entry(self):
        self._data[CONF_EV_CARS] = self._ev_cars
        return self.async_create_entry(title="Smart Energy Manager", data=self._data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmartEnergyOptionsFlow(config_entry)


# ══════════════════════════════════════════════════════════════════════════════
# OPTIONS FLOW  – fullständig, speglar alla 6 config-steg
# ══════════════════════════════════════════════════════════════════════════════

class SmartEnergyOptionsFlow(config_entries.OptionsFlow):
    """Options flow – redigera alla inställningar efter setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Merge data + options; options har prioritet
        self._base: dict[str, Any] = {**config_entry.data, **(config_entry.options or {})}
        self._data: dict[str, Any] = {}
        self._ev_cars: list[dict] = list(self._base.get(CONF_EV_CARS, []))
        # Index för bil vi redigerar just nu (-1 = ny bil)
        self._edit_car_index: int = -1

    # ── Steg 1: Nät & Prissättning ───────────────────────────────────

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_NORDPOOL_ENTITY):
                errors[CONF_NORDPOOL_ENTITY] = "required"
            else:
                self._data.update(_normalise(user_input))
                return await self.async_step_solar_opts()
        return self.async_show_form(
            step_id="init",
            data_schema=_grid_schema(self._base),
            errors=errors,
        )

    # ── Steg 2: Solceller ────────────────────────────────────────────

    async def async_step_solar_opts(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_battery_opts()
        return self.async_show_form(
            step_id="solar_opts",
            data_schema=_solar_schema(self._base),
        )

    # ── Steg 3: Batteri ──────────────────────────────────────────────

    async def async_step_battery_opts(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_heat_pump_opts()
        return self.async_show_form(
            step_id="battery_opts",
            data_schema=_battery_schema(self._base),
        )

    # ── Steg 4: Elpanna ──────────────────────────────────────────────

    async def async_step_heat_pump_opts(self, user_input=None):
        if user_input is not None:
            data = _normalise(user_input)
            comp_phase = data.get(CONF_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PHASE)
            data[CONF_HEAT_PUMP_PATRON_PHASES] = [p for p in ["L1", "L2", "L3"] if p != comp_phase]
            self._data.update(data)
            return await self.async_step_legionella_opts()
        return self.async_show_form(
            step_id="heat_pump_opts",
            data_schema=_heat_pump_schema(self._base),
        )

    # ── Steg 5: Legionella ───────────────────────────────────────────

    async def async_step_legionella_opts(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_ev_menu_opts()
        return self.async_show_form(
            step_id="legionella_opts",
            data_schema=_legionella_schema(self._base),
        )

    # ── Steg 6: Elbil-meny ───────────────────────────────────────────
    # Visar lista på befintliga bilar + val: lägg till / redigera / ta bort / klar

    async def async_step_ev_menu_opts(self, user_input=None):
        if user_input is not None:
            action = user_input.get("action", "done")
            if action == "add":
                self._edit_car_index = -1
                return await self.async_step_ev_car_opts()
            elif action == "done":
                self._data[CONF_EV_CARS] = self._ev_cars
                return self.async_create_entry(title="", data=self._data)
            elif action.startswith("edit_"):
                idx = int(action.split("_")[1])
                self._edit_car_index = idx
                return await self.async_step_ev_car_opts()
            elif action.startswith("delete_"):
                idx = int(action.split("_")[1])
                self._ev_cars.pop(idx)
                # Återgå till menyn
                return await self.async_step_ev_menu_opts()

        # Bygg dynamiska alternativ baserat på befintliga bilar
        options = []
        for i, car in enumerate(self._ev_cars):
            name = car.get("name", f"Bil {i+1}")
            options.append({"label": f"✏️  Redigera: {name}", "value": f"edit_{i}"})
            options.append({"label": f"🗑️  Ta bort: {name}", "value": f"delete_{i}"})
        options.append({"label": "➕  Lägg till ny bil", "value": "add"})
        options.append({"label": "✅  Klar – spara", "value": "done"})

        count = len(self._ev_cars)
        return self.async_show_form(
            step_id="ev_menu_opts",
            data_schema=vol.Schema({
                vol.Required("action", default="done"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
            description_placeholders={"count": str(count)},
        )

    # ── Steg 6b: Redigera / lägg till en bil ─────────────────────────

    async def async_step_ev_car_opts(self, user_input=None):
        errors = {}
        # Befintliga värden om vi redigerar
        existing = self._ev_cars[self._edit_car_index] if self._edit_car_index >= 0 else None

        if user_input is not None:
            if not user_input.get("car_name") or not user_input.get("charger_switch") or not user_input.get("charger_current"):
                errors["base"] = "required_fields"
            else:
                car = _car_dict_from_input(user_input)
                if self._edit_car_index >= 0:
                    self._ev_cars[self._edit_car_index] = car
                else:
                    self._ev_cars.append(car)
                self._edit_car_index = -1
                return await self.async_step_ev_menu_opts()

        title = f"Redigera: {existing['name']}" if existing else f"Ny bil {len(self._ev_cars) + 1}"
        return self.async_show_form(
            step_id="ev_car_opts",
            data_schema=_ev_car_schema(existing),
            errors=errors,
            description_placeholders={"title": title},
        )