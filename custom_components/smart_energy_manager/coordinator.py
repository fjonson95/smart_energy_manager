"""Data coordinator for Smart Energy Manager."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN, UPDATE_INTERVAL,
    CONF_BATTERY_SOC, CONF_BATTERY_INVERTER_CHARGE, CONF_BATTERY_INVERTER_DISCHARGE,
    CONF_BATTERY_INVERTER_POWER, CONF_BATTERY_CAPACITY_KWH, CONF_BATTERY_MAX_POWER_KW,
    CONF_SOLAR_INVERTER_TOTAL,
    CONF_SOLAR_INVERTER_POWER_L1, CONF_SOLAR_INVERTER_POWER_L2, CONF_SOLAR_INVERTER_POWER_L3,
    CONF_EV_CHARGER_SWITCH, CONF_EV_CHARGER_CURRENT, CONF_EV_CHARGER_POWER,
    CONF_EV_SOC, CONF_EV_CHARGER_PHASE,
    CONF_HEAT_PUMP_POWER, CONF_HEAT_PUMP_SWITCH, CONF_HEAT_PUMP_EXTRA_HOT_WATER,
    CONF_GRID_POWER_L1, CONF_GRID_POWER_L2, CONF_GRID_POWER_L3,
    CONF_GRID_CURRENT_L1, CONF_GRID_CURRENT_L2, CONF_GRID_CURRENT_L3,
    CONF_NORDPOOL_ENTITY, CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW,
    CONF_GRID_FEES, CONF_ENERGY_TAX, CONF_VAT_RATE, CONF_SELL_EXTRA_REVENUE,
    CONF_MAX_CURRENT_PER_PHASE, CONF_GRID_VOLTAGE,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC, CONF_EV_SOC_TARGET,
    CONF_WINTER_MODE_ENABLED,
    CONF_WINTER_CHEAP_HOUR_THRESHOLD, CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD,
    CONF_WINTER_MIN_SOC, CONF_WINTER_MAX_SOC,
    DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE, DEFAULT_VAT_RATE,
    DEFAULT_GRID_FEES, DEFAULT_ENERGY_TAX, DEFAULT_SELL_EXTRA_REVENUE,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC, DEFAULT_EV_SOC_TARGET,
    DEFAULT_WINTER_CHEAP_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD,
    DEFAULT_WINTER_MIN_SOC, DEFAULT_WINTER_MAX_SOC,
    MODE_AUTO,
)
from .energy_controller import EnergyController, EnergyState, ControlDecision

_LOGGER = logging.getLogger(__name__)


class SmartEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator that reads state and executes control decisions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.entry = entry
        self._config = {**entry.data, **entry.options}
        self.operating_mode: str = MODE_AUTO
        self.force_ev_charge: bool = False
        self._last_decision: Optional[ControlDecision] = None
        self._state: Optional[EnergyState] = None
        self._controller = self._build_controller()

    def _build_controller(self) -> EnergyController:
        c = self._config
        return EnergyController(
            max_current_per_phase=float(c.get(CONF_MAX_CURRENT_PER_PHASE, DEFAULT_MAX_CURRENT)),
            grid_voltage=float(c.get(CONF_GRID_VOLTAGE, DEFAULT_GRID_VOLTAGE)),
            battery_min_soc=float(c.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)),
            battery_max_soc=float(c.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC)),
            ev_soc_target=float(c.get(CONF_EV_SOC_TARGET, DEFAULT_EV_SOC_TARGET)),
            winter_cheap_threshold=float(c.get(CONF_WINTER_CHEAP_HOUR_THRESHOLD, DEFAULT_WINTER_CHEAP_THRESHOLD)),
            winter_expensive_threshold=float(c.get(CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD)),
            winter_min_soc=float(c.get(CONF_WINTER_MIN_SOC, DEFAULT_WINTER_MIN_SOC)),
            winter_max_soc=float(c.get(CONF_WINTER_MAX_SOC, DEFAULT_WINTER_MAX_SOC)),
        )

    def _get_state_float(self, entity_id: Optional[str], default: float = 0.0) -> float:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown", None):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _get_state_bool(self, entity_id: Optional[str]) -> bool:
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        return state.state.lower() in ("on", "true", "1", "home", "charging")

    def _get_nordpool_price(self) -> float:
        """Extract current hour spot price from nordpool sensor."""
        entity_id = self._config.get(CONF_NORDPOOL_ENTITY)
        if not entity_id:
            return 0.0
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return 0.0
        try:
            # nordpool integration stores current price in state
            return float(state.state)
        except (ValueError, TypeError):
            return 0.0

    def _get_solcast_forecast(self, entity_id: Optional[str]) -> float:
        if not entity_id:
            return 0.0
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return 0.0
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return 0.0

    async def _async_update_data(self) -> dict:
        """Fetch data and run control logic."""
        c = self._config
        try:
            spot_price = self._get_nordpool_price()
            grid_fees = float(c.get(CONF_GRID_FEES, DEFAULT_GRID_FEES))
            energy_tax = float(c.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX))
            vat_rate = float(c.get(CONF_VAT_RATE, DEFAULT_VAT_RATE))
            extra_revenue = float(c.get(CONF_SELL_EXTRA_REVENUE, DEFAULT_SELL_EXTRA_REVENUE))

            buy_price = self._controller.calculate_buy_price(spot_price, grid_fees, energy_tax, vat_rate)
            sell_price = self._controller.calculate_sell_price(spot_price, extra_revenue)

            state = EnergyState(
                solar_power_w=self._get_state_float(c.get(CONF_SOLAR_INVERTER_TOTAL)),
                solar_power_l1=self._get_state_float(c.get(CONF_SOLAR_INVERTER_POWER_L1)),
                solar_power_l2=self._get_state_float(c.get(CONF_SOLAR_INVERTER_POWER_L2)),
                solar_power_l3=self._get_state_float(c.get(CONF_SOLAR_INVERTER_POWER_L3)),
                solar_forecast_today_kwh=self._get_solcast_forecast(c.get(CONF_SOLCAST_TODAY)),
                solar_forecast_tomorrow_kwh=self._get_solcast_forecast(c.get(CONF_SOLCAST_TOMORROW)),

                battery_soc_pct=self._get_state_float(c.get(CONF_BATTERY_SOC), default=50.0),
                battery_power_w=self._get_state_float(c.get(CONF_BATTERY_INVERTER_POWER)),
                battery_capacity_kwh=float(c.get(CONF_BATTERY_CAPACITY_KWH, 10.0)),
                battery_max_power_kw=float(c.get(CONF_BATTERY_MAX_POWER_KW, 5.0)),

                ev_charging=self._get_state_bool(c.get(CONF_EV_CHARGER_SWITCH)),
                ev_current_a=self._get_state_float(c.get(CONF_EV_CHARGER_CURRENT)),
                ev_power_w=self._get_state_float(c.get(CONF_EV_CHARGER_POWER)),
                ev_soc_pct=self._get_state_float(c.get(CONF_EV_SOC)) or None,
                ev_phase=c.get(CONF_EV_CHARGER_PHASE, "L1"),

                heat_pump_power_w=self._get_state_float(c.get(CONF_HEAT_PUMP_POWER)),
                heat_pump_on=self._get_state_bool(c.get(CONF_HEAT_PUMP_SWITCH)),
                extra_hot_water_on=self._get_state_bool(c.get(CONF_HEAT_PUMP_EXTRA_HOT_WATER)),

                grid_power_l1=self._get_state_float(c.get(CONF_GRID_POWER_L1)),
                grid_power_l2=self._get_state_float(c.get(CONF_GRID_POWER_L2)),
                grid_power_l3=self._get_state_float(c.get(CONF_GRID_POWER_L3)),
                grid_current_l1=self._get_state_float(c.get(CONF_GRID_CURRENT_L1)),
                grid_current_l2=self._get_state_float(c.get(CONF_GRID_CURRENT_L2)),
                grid_current_l3=self._get_state_float(c.get(CONF_GRID_CURRENT_L3)),

                spot_price_sek_kwh=spot_price,
                buy_price_sek_kwh=buy_price,
                sell_price_sek_kwh=sell_price,

                operating_mode=self.operating_mode,
                force_ev_charge=self.force_ev_charge,
                winter_mode=bool(c.get(CONF_WINTER_MODE_ENABLED, False)),
            )
            self._state = state

            decision = self._controller.compute(state)
            self._last_decision = decision

            # Execute the decision
            await self._execute_decision(state, decision)

            return {
                "state": state,
                "decision": decision,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "spot_price": spot_price,
            }

        except Exception as err:
            raise UpdateFailed(f"Error updating Smart Energy Manager: {err}") from err

    async def _execute_decision(self, state: EnergyState, decision: ControlDecision) -> None:
        """Apply control decisions to actual devices."""
        # --- Battery charge ---
        charge_entity = self._config.get(CONF_BATTERY_INVERTER_CHARGE)
        discharge_entity = self._config.get(CONF_BATTERY_INVERTER_DISCHARGE)

        if charge_entity:
            if decision.battery_charge_power_w > 0:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": charge_entity, "value": decision.battery_charge_power_w},
                    blocking=False,
                )
            else:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": charge_entity, "value": 0},
                    blocking=False,
                )

        if discharge_entity:
            if decision.battery_discharge_power_w > 0:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": discharge_entity, "value": decision.battery_discharge_power_w},
                    blocking=False,
                )
            else:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": discharge_entity, "value": 0},
                    blocking=False,
                )

        # --- EV charger ---
        ev_switch = self._config.get(CONF_EV_CHARGER_SWITCH)
        ev_current_entity = self._config.get(CONF_EV_CHARGER_CURRENT)

        if ev_current_entity and decision.ev_enable and decision.ev_current_a > 0:
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": ev_current_entity, "value": decision.ev_current_a},
                blocking=False,
            )

        if ev_switch:
            service = "turn_on" if decision.ev_enable else "turn_off"
            await self.hass.services.async_call(
                "switch", service,
                {"entity_id": ev_switch},
                blocking=False,
            )

        # --- Extra hot water ---
        hot_water_entity = self._config.get(CONF_HEAT_PUMP_EXTRA_HOT_WATER)
        if hot_water_entity:
            service = "turn_on" if decision.extra_hot_water else "turn_off"
            await self.hass.services.async_call(
                "switch", service,
                {"entity_id": hot_water_entity},
                blocking=False,
            )

    @property
    def last_decision(self) -> Optional[ControlDecision]:
        return self._last_decision

    @property
    def current_state(self) -> Optional[EnergyState]:
        return self._state
