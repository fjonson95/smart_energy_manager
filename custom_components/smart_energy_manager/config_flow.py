"""Config flow for Smart Energy Manager."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_BATTERY_INVERTER_POWER, CONF_BATTERY_INVERTER_CHARGE, CONF_BATTERY_INVERTER_DISCHARGE,
    CONF_BATTERY_SOC, CONF_BATTERY_CAPACITY_KWH, CONF_BATTERY_MAX_POWER_KW,
    CONF_SOLAR_INVERTER_TOTAL,
    CONF_SOLAR_INVERTER_POWER_L1, CONF_SOLAR_INVERTER_POWER_L2, CONF_SOLAR_INVERTER_POWER_L3,
    CONF_EV_CHARGERS,
    CONF_HEAT_PUMP_POWER, CONF_HEAT_PUMP_SWITCH, CONF_HEAT_PUMP_EXTRA_HOT_WATER,
    CONF_HEAT_PUMP_PHASE, CONF_HEAT_PUMP_PATRON_PHASES, CONF_HEAT_PUMP_PATRON_POWER_KW,
    CONF_GRID_POWER_L1, CONF_GRID_POWER_L2, CONF_GRID_POWER_L3,
    CONF_GRID_CURRENT_L1, CONF_GRID_CURRENT_L2, CONF_GRID_CURRENT_L3,
    CONF_NORDPOOL_ENTITY, CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW,
    CONF_GRID_FEES, CONF_ENERGY_TAX, CONF_VAT_RATE, CONF_SELL_EXTRA_REVENUE,
    CONF_MAX_CURRENT_PER_PHASE, CONF_GRID_VOLTAGE,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC,
    DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE, DEFAULT_VAT_RATE,
    DEFAULT_GRID_FEES, DEFAULT_ENERGY_TAX, DEFAULT_SELL_EXTRA_REVENUE,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC,
    DEFAULT_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PATRON_POWER_KW,
    CONF_HOUSE_LOAD_ENTITY, CONF_GRID_POWER_UNIT, CONF_EV_POWER_UNIT, UNIT_W, UNIT_KW,
    CONF_LEGIONELLA_ENABLED, CONF_LEGIONELLA_INTERVAL_DAYS,
    CONF_LEGIONELLA_PREFERRED_HOUR_START, CONF_LEGIONELLA_PREFERRED_HOUR_END,
    CONF_LEGIONELLA_MAX_PRICE, CONF_LEGIONELLA_DURATION_MINUTES,
    DEFAULT_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_INTERVAL_DAYS,
    DEFAULT_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_END,
    DEFAULT_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_DURATION_MINUTES,
    EV_PHASES_OPTIONS,
    DEFAULT_WINTER_CHEAP_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD,
    DEFAULT_WINTER_MIN_SOC, DEFAULT_WINTER_MAX_SOC,
    CONF_WINTER_CHEAP_HOUR_THRESHOLD, CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD,
    CONF_WINTER_MIN_SOC, CONF_WINTER_MAX_SOC,
)

_LOGGER = logging.getLogger(__name__)


# ── Selector-hjälpare ─────────────────────────────────────────────────────────

def _entity_selector(domain=None) -> selector.EntitySelector:
    if domain:
        domains = domain if isinstance(domain, list) else [domain]
        return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))
    return selector.EntitySelector()


def _opt_entity_selector():
    """Valfri entitet – textfält som kan lämnas tomt."""
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT))


def _d(data: dict, key: str, default=None):
    return data.get(key, default)


def _normalise(data: dict) -> dict:
    """Konvertera tomma strängar till None för entitetsfält."""
    return {k: (None if isinstance(v, str) and v.strip() == "" else v) for k, v in data.items()}


# ── Stegscheman ──────────────────────────────────────────────────────────────

def _grid_schema(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_NORDPOOL_ENTITY, default=_d(d, CONF_NORDPOOL_ENTITY, "")): _entity_selector("sensor"),
        vol.Optional(CONF_GRID_POWER_L1, default=_d(d, CONF_GRID_POWER_L1, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_POWER_L2, default=_d(d, CONF_GRID_POWER_L2, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_POWER_L3, default=_d(d, CONF_GRID_POWER_L3, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_CURRENT_L1, default=_d(d, CONF_GRID_CURRENT_L1, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_CURRENT_L2, default=_d(d, CONF_GRID_CURRENT_L2, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_CURRENT_L3, default=_d(d, CONF_GRID_CURRENT_L3, "")): _opt_entity_selector(),
        vol.Optional(CONF_MAX_CURRENT_PER_PHASE, default=_d(d, CONF_MAX_CURRENT_PER_PHASE, DEFAULT_MAX_CURRENT)): vol.Coerce(float),
        vol.Optional(CONF_GRID_VOLTAGE, default=_d(d, CONF_GRID_VOLTAGE, DEFAULT_GRID_VOLTAGE)): vol.Coerce(float),
        vol.Optional(CONF_GRID_FEES, default=_d(d, CONF_GRID_FEES, DEFAULT_GRID_FEES)): vol.Coerce(float),
        vol.Optional(CONF_ENERGY_TAX, default=_d(d, CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX)): vol.Coerce(float),
        vol.Optional(CONF_VAT_RATE, default=_d(d, CONF_VAT_RATE, DEFAULT_VAT_RATE)): vol.Coerce(float),
        vol.Optional(CONF_SELL_EXTRA_REVENUE, default=_d(d, CONF_SELL_EXTRA_REVENUE, DEFAULT_SELL_EXTRA_REVENUE)): vol.Coerce(float),
        vol.Optional(CONF_HOUSE_LOAD_ENTITY, default=_d(d, CONF_HOUSE_LOAD_ENTITY, "")): _opt_entity_selector(),
        vol.Optional(CONF_GRID_POWER_UNIT, default=_d(d, CONF_GRID_POWER_UNIT, UNIT_W)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[{"label": "Watt (W)", "value": "W"}, {"label": "Kilowatt (kW)", "value": "kW"}],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_EV_POWER_UNIT, default=_d(d, CONF_EV_POWER_UNIT, UNIT_W)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[{"label": "Watt (W)", "value": "W"}, {"label": "Kilowatt (kW)", "value": "kW"}],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    })


def _solar_schema(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_SOLAR_INVERTER_TOTAL, default=_d(d, CONF_SOLAR_INVERTER_TOTAL, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L1, default=_d(d, CONF_SOLAR_INVERTER_POWER_L1, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L2, default=_d(d, CONF_SOLAR_INVERTER_POWER_L2, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLAR_INVERTER_POWER_L3, default=_d(d, CONF_SOLAR_INVERTER_POWER_L3, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLCAST_TODAY, default=_d(d, CONF_SOLCAST_TODAY, "")): _opt_entity_selector(),
        vol.Optional(CONF_SOLCAST_TOMORROW, default=_d(d, CONF_SOLCAST_TOMORROW, "")): _opt_entity_selector(),
    })


def _battery_schema(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_BATTERY_SOC, default=_d(d, CONF_BATTERY_SOC, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_INVERTER_POWER, default=_d(d, CONF_BATTERY_INVERTER_POWER, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_INVERTER_CHARGE, default=_d(d, CONF_BATTERY_INVERTER_CHARGE, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_INVERTER_DISCHARGE, default=_d(d, CONF_BATTERY_INVERTER_DISCHARGE, "")): _opt_entity_selector(),
        vol.Optional(CONF_BATTERY_CAPACITY_KWH, default=_d(d, CONF_BATTERY_CAPACITY_KWH, 10.0)): vol.Coerce(float),
        vol.Optional(CONF_BATTERY_MAX_POWER_KW, default=_d(d, CONF_BATTERY_MAX_POWER_KW, 5.0)): vol.Coerce(float),
        vol.Optional(CONF_BATTERY_MIN_SOC, default=_d(d, CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)): vol.Coerce(float),
        vol.Optional(CONF_BATTERY_MAX_SOC, default=_d(d, CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC)): vol.Coerce(float),
    })


def _heat_pump_schema(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_HEAT_PUMP_POWER, default=_d(d, CONF_HEAT_PUMP_POWER, "")): _opt_entity_selector(),
        vol.Optional(CONF_HEAT_PUMP_SWITCH, default=_d(d, CONF_HEAT_PUMP_SWITCH, "")): _opt_entity_selector(),
        vol.Optional(CONF_HEAT_PUMP_EXTRA_HOT_WATER, default=_d(d, CONF_HEAT_PUMP_EXTRA_HOT_WATER, "")): _opt_entity_selector(),
        vol.Optional(CONF_HEAT_PUMP_PHASE, default=_d(d, CONF_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PHASE)): selector.SelectSelector(
            selector.SelectSelectorConfig(options=EV_PHASES_OPTIONS, mode=selector.SelectSelectorMode.LIST)
        ),
        vol.Optional(CONF_HEAT_PUMP_PATRON_POWER_KW, default=_d(d, CONF_HEAT_PUMP_PATRON_POWER_KW, DEFAULT_HEAT_PUMP_PATRON_POWER_KW)): vol.Coerce(float),
    })


def _legionella_schema(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_LEGIONELLA_ENABLED, default=_d(d, CONF_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_ENABLED)): bool,
        vol.Optional(CONF_LEGIONELLA_INTERVAL_DAYS, default=_d(d, CONF_LEGIONELLA_INTERVAL_DAYS, DEFAULT_LEGIONELLA_INTERVAL_DAYS)): vol.Coerce(int),
        vol.Optional(CONF_LEGIONELLA_PREFERRED_HOUR_START, default=_d(d, CONF_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_START)): vol.Coerce(int),
        vol.Optional(CONF_LEGIONELLA_PREFERRED_HOUR_END, default=_d(d, CONF_LEGIONELLA_PREFERRED_HOUR_END, DEFAULT_LEGIONELLA_PREFERRED_HOUR_END)): vol.Coerce(int),
        vol.Optional(CONF_LEGIONELLA_MAX_PRICE, default=_d(d, CONF_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_MAX_PRICE)): vol.Coerce(float),
        vol.Optional(CONF_LEGIONELLA_DURATION_MINUTES, default=_d(d, CONF_LEGIONELLA_DURATION_MINUTES, DEFAULT_LEGIONELLA_DURATION_MINUTES)): vol.Coerce(int),
    })


def _charger_schema(d: dict) -> vol.Schema:
    """Schema för en laddare (hårdvara)."""
    return vol.Schema({
        vol.Required("charger_name", default=_d(d, "name", "")): str,
        vol.Optional("connected_sensor", default=_d(d, "connected_sensor", "")): _opt_entity_selector(),
        vol.Required("charger_switch", default=_d(d, "charger_switch", "")): _opt_entity_selector(),
        vol.Required("charger_current", default=_d(d, "charger_current", "")): _opt_entity_selector(),
        vol.Optional("charger_power", default=_d(d, "charger_power", "")): _opt_entity_selector(),
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


def _car_schema(d: dict) -> vol.Schema:
    """Schema för en bil (kopplad till en laddare)."""
    return vol.Schema({
        vol.Required("car_name", default=_d(d, "name", "")): str,
        vol.Optional("ev_soc", default=_d(d, "ev_soc", "")): _opt_entity_selector(),
        vol.Optional("ev_soc_target", default=_d(d, "ev_soc_target", 80.0)): vol.Coerce(float),
        vol.Optional("phase", default=_d(d, "phase", "L1") or "L1"): selector.SelectSelector(
            selector.SelectSelectorConfig(options=EV_PHASES_OPTIONS, mode=selector.SelectSelectorMode.LIST)
        ),
    })


def _charger_dict_from_input(ui: dict, cars: list[dict]) -> dict:
    phases = int(ui.get("phases", 1))
    return {
        "name": ui["charger_name"],
        "connected_sensor": ui.get("connected_sensor") or None,
        "charger_switch": ui.get("charger_switch") or None,
        "charger_current": ui.get("charger_current") or None,
        "charger_power": ui.get("charger_power") or None,
        "phases": phases,
        "phase": ui.get("phase") if phases == 1 else None,
        "cars": cars,
    }


def _car_dict_from_input(ui: dict) -> dict:
    return {
        "name": ui["car_name"],
        "ev_soc": ui.get("ev_soc") or None,
        "ev_soc_target": float(ui.get("ev_soc_target", 80.0)),
        "phase": ui.get("phase") or None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG FLOW
# ══════════════════════════════════════════════════════════════════════════════

class SmartEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._chargers: list[dict] = []
        # State för laddare/bil-under-konfigurering
        self._current_charger_ui: dict = {}
        self._current_charger_cars: list[dict] = []
        self._edit_charger_index: int = -1
        self._edit_car_index: int = -1

    # ── Steg 1–5 (nät, sol, batteri, elpanna, legionella) ────────────

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_NORDPOOL_ENTITY):
                errors[CONF_NORDPOOL_ENTITY] = "required"
            else:
                self._data.update(_normalise(user_input))
                return await self.async_step_solar()
        return self.async_show_form(step_id="user", data_schema=_grid_schema({}), errors=errors)

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
            return await self.async_step_charger_menu()
        return self.async_show_form(step_id="legionella", data_schema=_legionella_schema({}))

    # ── Steg 6: Laddarmeny ───────────────────────────────────────────

    async def async_step_charger_menu(self, user_input=None):
        if user_input is not None:
            if user_input.get("add_charger"):
                self._current_charger_ui = {}
                self._current_charger_cars = []
                self._edit_charger_index = -1
                return await self.async_step_charger()
            else:
                self._data[CONF_EV_CHARGERS] = self._chargers
                return self.async_create_entry(title="Smart Energy Manager", data=self._data)

        added = len(self._chargers)
        return self.async_show_form(
            step_id="charger_menu",
            data_schema=vol.Schema({vol.Required("add_charger", default=added == 0): bool}),
            description_placeholders={"count": str(added)},
        )

    # ── Steg 6a: Konfigurera laddare ─────────────────────────────────

    async def async_step_charger(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get("charger_name"):
                errors["charger_name"] = "required"
            else:
                self._current_charger_ui = _normalise(user_input)
                self._current_charger_cars = []
                return await self.async_step_car_menu()
        return self.async_show_form(
            step_id="charger",
            data_schema=_charger_schema({}),
            errors=errors,
            description_placeholders={"title": f"Laddare {len(self._chargers) + 1}"},
        )

    # ── Steg 6b: Bil-meny (för aktuell laddare) ──────────────────────

    async def async_step_car_menu(self, user_input=None):
        if user_input is not None:
            if user_input.get("add_car"):
                self._edit_car_index = -1
                return await self.async_step_car()
            else:
                # Spara laddaren med sina bilar
                charger = _charger_dict_from_input(self._current_charger_ui, self._current_charger_cars)
                self._chargers.append(charger)
                return await self.async_step_charger_menu()

        added = len(self._current_charger_cars)
        charger_name = self._current_charger_ui.get("charger_name", "Laddare")
        return self.async_show_form(
            step_id="car_menu",
            data_schema=vol.Schema({vol.Required("add_car", default=added == 0): bool}),
            description_placeholders={"count": str(added), "charger": charger_name},
        )

    # ── Steg 6c: Konfigurera bil ─────────────────────────────────────

    async def async_step_car(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get("car_name"):
                errors["car_name"] = "required"
            else:
                car = _car_dict_from_input(_normalise(user_input))
                self._current_charger_cars.append(car)
                return await self.async_step_car_menu()
        return self.async_show_form(
            step_id="car",
            data_schema=_car_schema({}),
            errors=errors,
            description_placeholders={"title": f"Bil {len(self._current_charger_cars) + 1}"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmartEnergyOptionsFlow(config_entry)


# ══════════════════════════════════════════════════════════════════════════════
# OPTIONS FLOW
# ══════════════════════════════════════════════════════════════════════════════

class SmartEnergyOptionsFlow(config_entries.OptionsFlow):

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._base: dict[str, Any] = {**config_entry.data, **(config_entry.options or {})}
        self._data: dict[str, Any] = {}
        self._chargers: list[dict] = list(self._base.get(CONF_EV_CHARGERS, []))
        self._current_charger_ui: dict = {}
        self._current_charger_cars: list[dict] = []
        self._edit_charger_index: int = -1
        self._edit_car_index: int = -1

    # ── Steg 1–5 ─────────────────────────────────────────────────────

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_NORDPOOL_ENTITY):
                errors[CONF_NORDPOOL_ENTITY] = "required"
            else:
                self._data.update(_normalise(user_input))
                return await self.async_step_solar_opts()
        return self.async_show_form(step_id="init", data_schema=_grid_schema(self._base), errors=errors)

    async def async_step_solar_opts(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_battery_opts()
        return self.async_show_form(step_id="solar_opts", data_schema=_solar_schema(self._base))

    async def async_step_battery_opts(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_heat_pump_opts()
        return self.async_show_form(step_id="battery_opts", data_schema=_battery_schema(self._base))

    async def async_step_heat_pump_opts(self, user_input=None):
        if user_input is not None:
            data = _normalise(user_input)
            comp_phase = data.get(CONF_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PHASE)
            data[CONF_HEAT_PUMP_PATRON_PHASES] = [p for p in ["L1", "L2", "L3"] if p != comp_phase]
            self._data.update(data)
            return await self.async_step_legionella_opts()
        return self.async_show_form(step_id="heat_pump_opts", data_schema=_heat_pump_schema(self._base))

    async def async_step_legionella_opts(self, user_input=None):
        if user_input is not None:
            self._data.update(_normalise(user_input))
            return await self.async_step_charger_menu_opts()
        return self.async_show_form(step_id="legionella_opts", data_schema=_legionella_schema(self._base))

    # ── Steg 6: Laddarmeny ───────────────────────────────────────────

    async def async_step_charger_menu_opts(self, user_input=None):
        if user_input is not None:
            action = user_input.get("action", "done")
            if action == "add":
                self._current_charger_ui = {}
                self._current_charger_cars = []
                self._edit_charger_index = -1
                return await self.async_step_charger_opts()
            elif action == "done":
                self._data[CONF_EV_CHARGERS] = self._chargers
                return self.async_create_entry(title="", data=self._data)
            elif action.startswith("edit_"):
                idx = int(action.split("_")[1])
                self._edit_charger_index = idx
                ch = self._chargers[idx]
                self._current_charger_ui = {
                    "charger_name": ch.get("name", ""),
                    **{k: ch.get(k, "") for k in ["connected_sensor", "charger_switch", "charger_current", "charger_power", "phase"]},
                    "phases": str(ch.get("phases", 1)),
                }
                self._current_charger_cars = list(ch.get("cars", []))
                return await self.async_step_charger_opts()
            elif action.startswith("delete_"):
                idx = int(action.split("_")[1])
                self._chargers.pop(idx)
                return await self.async_step_charger_menu_opts()

        options = []
        for i, ch in enumerate(self._chargers):
            name = ch.get("name", f"Laddare {i+1}")
            cars = ch.get("cars", [])
            car_names = ", ".join(c.get("name", "") for c in cars) or "inga bilar"
            options.append({"label": f"✏️  Redigera: {name} ({car_names})", "value": f"edit_{i}"})
            options.append({"label": f"🗑️  Ta bort: {name}", "value": f"delete_{i}"})
        options.append({"label": "➕  Lägg till laddare", "value": "add"})
        options.append({"label": "✅  Klar – spara", "value": "done"})

        return self.async_show_form(
            step_id="charger_menu_opts",
            data_schema=vol.Schema({
                vol.Required("action", default="done"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)
                ),
            }),
            description_placeholders={"count": str(len(self._chargers))},
        )

    # ── Steg 6a: Redigera/lägg till laddare ──────────────────────────

    async def async_step_charger_opts(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get("charger_name"):
                errors["charger_name"] = "required"
            else:
                self._current_charger_ui = _normalise(user_input)
                if self._edit_charger_index < 0:
                    self._current_charger_cars = []
                return await self.async_step_car_menu_opts()
        existing = self._current_charger_ui or {}
        title = f"Redigera: {existing.get('charger_name', '')}" if self._edit_charger_index >= 0 else f"Ny laddare {len(self._chargers) + 1}"
        return self.async_show_form(
            step_id="charger_opts",
            data_schema=_charger_schema(existing),
            errors=errors,
            description_placeholders={"title": title},
        )

    # ── Steg 6b: Bil-meny per laddare ────────────────────────────────

    async def async_step_car_menu_opts(self, user_input=None):
        if user_input is not None:
            action = user_input.get("action", "done")
            if action == "add":
                self._edit_car_index = -1
                return await self.async_step_car_opts()
            elif action == "done":
                charger = _charger_dict_from_input(self._current_charger_ui, self._current_charger_cars)
                if self._edit_charger_index >= 0:
                    self._chargers[self._edit_charger_index] = charger
                else:
                    self._chargers.append(charger)
                self._edit_charger_index = -1
                return await self.async_step_charger_menu_opts()
            elif action.startswith("edit_"):
                self._edit_car_index = int(action.split("_")[1])
                return await self.async_step_car_opts()
            elif action.startswith("delete_"):
                self._current_charger_cars.pop(int(action.split("_")[1]))
                return await self.async_step_car_menu_opts()

        charger_name = self._current_charger_ui.get("charger_name", "Laddare")
        options = []
        for i, car in enumerate(self._current_charger_cars):
            name = car.get("name", f"Bil {i+1}")
            options.append({"label": f"✏️  Redigera: {name}", "value": f"edit_{i}"})
            options.append({"label": f"🗑️  Ta bort: {name}", "value": f"delete_{i}"})
        options.append({"label": "➕  Lägg till bil", "value": "add"})
        options.append({"label": f"✅  Klar med bilar för {charger_name}", "value": "done"})

        return self.async_show_form(
            step_id="car_menu_opts",
            data_schema=vol.Schema({
                vol.Required("action", default="done"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)
                ),
            }),
            description_placeholders={"count": str(len(self._current_charger_cars)), "charger": charger_name},
        )

    # ── Steg 6c: Redigera/lägg till bil ──────────────────────────────

    async def async_step_car_opts(self, user_input=None):
        errors = {}
        existing = self._current_charger_cars[self._edit_car_index] if self._edit_car_index >= 0 else {}
        if user_input is not None:
            if not user_input.get("car_name"):
                errors["car_name"] = "required"
            else:
                car = _car_dict_from_input(_normalise(user_input))
                if self._edit_car_index >= 0:
                    self._current_charger_cars[self._edit_car_index] = car
                else:
                    self._current_charger_cars.append(car)
                self._edit_car_index = -1
                return await self.async_step_car_menu_opts()
        title = f"Redigera: {existing.get('name', '')}" if self._edit_car_index >= 0 else f"Ny bil {len(self._current_charger_cars) + 1}"
        return self.async_show_form(
            step_id="car_opts",
            data_schema=_car_schema(existing),
            errors=errors,
            description_placeholders={"title": title},
        )
