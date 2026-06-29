"""Data coordinator for Smart Energy Manager."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HOT_WATER_TEMP_ENTITY, CONF_LEGIONELLA_SWITCH,
    CONF_EXTRA_HOT_WATER_MAX_TEMP, CONF_LEGIONELLA_TARGET_TEMP,
    DEFAULT_EXTRA_HOT_WATER_MAX_TEMP, DEFAULT_LEGIONELLA_TARGET_TEMP,
    DOMAIN, UPDATE_INTERVAL,
    CONF_BATTERY_SOC, CONF_BATTERY_INVERTER_CHARGE, CONF_BATTERY_INVERTER_DISCHARGE,
    CONF_BATTERY_INVERTER_POWER, CONF_BATTERY_CAPACITY_KWH, CONF_BATTERY_MAX_POWER_KW,
    CONF_SOLAR_INVERTER_TOTAL,
    CONF_SOLAR_INVERTER_POWER_L1, CONF_SOLAR_INVERTER_POWER_L2, CONF_SOLAR_INVERTER_POWER_L3,
    CONF_EV_CHARGERS, CONF_EV_CARS,
    CONF_HEAT_PUMP_POWER, CONF_HEAT_PUMP_SWITCH, CONF_HEAT_PUMP_EXTRA_HOT_WATER,
    CONF_HEAT_PUMP_PHASE, CONF_HEAT_PUMP_PATRON_PHASES, CONF_HEAT_PUMP_PATRON_POWER_KW,
    CONF_GRID_POWER_L1, CONF_GRID_POWER_L2, CONF_GRID_POWER_L3,
    CONF_GRID_CURRENT_L1, CONF_GRID_CURRENT_L2, CONF_GRID_CURRENT_L3,
    CONF_NORDPOOL_ENTITY, CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW,
    CONF_GRID_FEES, CONF_ENERGY_TAX, CONF_VAT_RATE, CONF_SELL_EXTRA_REVENUE,
    CONF_MAX_CURRENT_PER_PHASE, CONF_GRID_VOLTAGE,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC,
    CONF_WINTER_MODE_ENABLED,
    CONF_WINTER_CHEAP_HOUR_THRESHOLD, CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD,
    CONF_WINTER_MIN_SOC, CONF_WINTER_MAX_SOC,
    CONF_HOUSE_LOAD_ENTITY, CONF_GRID_POWER_UNIT, CONF_EV_POWER_UNIT,
    UNIT_W, UNIT_KW,
    DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE, DEFAULT_VAT_RATE,
    DEFAULT_GRID_FEES, DEFAULT_ENERGY_TAX, DEFAULT_SELL_EXTRA_REVENUE,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC,
    DEFAULT_WINTER_CHEAP_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD,
    DEFAULT_WINTER_MIN_SOC, DEFAULT_WINTER_MAX_SOC,
    DEFAULT_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PATRON_PHASES, DEFAULT_HEAT_PUMP_PATRON_POWER_KW,
    CHARGER_CONNECTED_STATES, NO_CAR_SELECTED,
    MODE_AUTO,
)
from .energy_controller import (
    EnergyController, EnergyState, ControlDecision,
    ChargerConfig, CarConfig, ChargerState,
)
from .legionella import LegionellaManager

_LOGGER = logging.getLogger(__name__)


def _migrate_ev_cars_to_chargers(ev_cars: list[dict]) -> list[dict]:
    """
    Bakåtkompatibilitet: konvertera gamla ev_cars-strukturen till nya ev_chargers.
    Varje gammal bil blir en laddare med en bil i sin car-lista.
    """
    chargers = []
    for car in ev_cars:
        chargers.append({
            "name": car.get("name", "Laddare"),
            "connected_sensor": None,
            "charger_switch": car.get("charger_switch", ""),
            "charger_current": car.get("charger_current", ""),
            "charger_power": car.get("charger_power"),
            "phases": car.get("phases", 1),
            "phase": car.get("phase"),
            "cars": [{
                "name": car.get("name", "Bil"),
                "ev_soc": car.get("ev_soc"),
                "ev_soc_target": car.get("ev_soc_target", 80.0),
                "car_phases": car.get("phases", 1),  # migration: gammal bil-fas → car_phases
                "phase": car.get("phase"),
            }],
            # Vid migration: sätt bilen som automatiskt vald (bara 1 bil per laddare)
            "_auto_select_car": car.get("name", "Bil"),
        })
    return chargers


class SmartEnergyCoordinator(DataUpdateCoordinator):
    """Koordinator som läser tillstånd och utför styrningsbeslut."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.entry = entry
        self._config = {**entry.data, **entry.options}
        self.operating_mode: str = MODE_AUTO
        self._last_decision: Optional[ControlDecision] = None
        self._state: Optional[EnergyState] = None
        self._controller = self._build_controller()
        self._legionella = LegionellaManager(hass, self._config)

        self._grid_scale = 1000.0 if self._config.get(CONF_GRID_POWER_UNIT, UNIT_W) == UNIT_KW else 1.0
        self._ev_scale   = 1000.0 if self._config.get(CONF_EV_POWER_UNIT,   UNIT_W) == UNIT_KW else 1.0

        # active_car[charger_name] = car_name eller NO_CAR_SELECTED
        # Styrs av select-entiteten i select.py
        self._active_cars: dict[str, str] = {}
        self._init_active_cars()

    def _init_active_cars(self) -> None:
        """Initiera bilval för alla laddare."""
        chargers = self._get_charger_configs()
        for ch_data in chargers:
            name = ch_data.get("name", "")
            # Vid migration med _auto_select_car: välj bilen direkt
            auto = ch_data.get("_auto_select_car")
            if name not in self._active_cars:
                self._active_cars[name] = auto if auto else NO_CAR_SELECTED

    def _get_charger_configs(self) -> list[dict]:
        """Hämta laddarkonfiguration, med fallback till gamla ev_cars."""
        if CONF_EV_CHARGERS in self._config:
            return self._config[CONF_EV_CHARGERS]
        # Bakåtkompatibilitet
        ev_cars = self._config.get(CONF_EV_CARS, [])
        return _migrate_ev_cars_to_chargers(ev_cars)

    async def async_config_entry_first_refresh(self):
        await self._legionella.async_load()
        await super().async_config_entry_first_refresh()

    def _build_controller(self) -> EnergyController:
        c = self._config
        return EnergyController(
            max_current_per_phase=float(c.get(CONF_MAX_CURRENT_PER_PHASE, DEFAULT_MAX_CURRENT)),
            grid_voltage=float(c.get(CONF_GRID_VOLTAGE, DEFAULT_GRID_VOLTAGE)),
            battery_min_soc=float(c.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)),
            battery_max_soc=float(c.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC)),
            winter_cheap_threshold=float(c.get(CONF_WINTER_CHEAP_HOUR_THRESHOLD, DEFAULT_WINTER_CHEAP_THRESHOLD)),
            winter_expensive_threshold=float(c.get(CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD, DEFAULT_WINTER_EXPENSIVE_THRESHOLD)),
            winter_min_soc=float(c.get(CONF_WINTER_MIN_SOC, DEFAULT_WINTER_MIN_SOC)),
            winter_max_soc=float(c.get(CONF_WINTER_MAX_SOC, DEFAULT_WINTER_MAX_SOC)),
        )

    # ── Avläsningshjälpare ────────────────────────────────────────────

    def _get_state_float(self, entity_id: Optional[str], default: float = 0.0) -> float:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
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

    def _is_charger_connected(self, entity_id: Optional[str]) -> bool:
        """Kontrollera om laddarsensorn indikerar att en bil är ansluten."""
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return False
        return state.state.lower() in CHARGER_CONNECTED_STATES

    def _get_grid_power_w(self, entity_id: Optional[str]) -> float:
        return self._get_state_float(entity_id) * self._grid_scale

    def _get_ev_power_w(self, entity_id: Optional[str]) -> float:
        return self._get_state_float(entity_id) * self._ev_scale

    def _get_nordpool_price(self) -> float:
        entity_id = self._config.get(CONF_NORDPOOL_ENTITY)
        if not entity_id:
            return 0.0
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return 0.0
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return 0.0

    def _build_charger_states(self) -> list[ChargerState]:
        charger_cfgs = self._get_charger_configs()
        result: list[ChargerState] = []

        for ch_data in charger_cfgs:
            cars = [
                CarConfig(
                    name=car.get("name", "Bil"),
                    ev_soc=car.get("ev_soc") or None,
                    ev_soc_target=float(car.get("ev_soc_target", 80.0)),
                    car_phases=int(car.get("car_phases", 1)),
                    phase=car.get("phase") or None,
                )
                for car in ch_data.get("cars", [])
            ]

            cfg = ChargerConfig(
                name=ch_data.get("name", "Laddare"),
                charger_switch=ch_data.get("charger_switch", ""),
                charger_current=ch_data.get("charger_current", ""),
                connected_sensor=ch_data.get("connected_sensor") or None,
                charger_power=ch_data.get("charger_power") or None,
                phases=int(ch_data.get("phases", 1)),
                phase=ch_data.get("phase") or None,
                cars=cars,
            )

            connected = self._is_charger_connected(cfg.connected_sensor)
            active_car_name = self._active_cars.get(cfg.name, NO_CAR_SELECTED)

            # SOC för aktiv bil
            soc_pct: Optional[float] = None
            for car in cars:
                if car.name == active_car_name and car.ev_soc:
                    raw = self._get_state_float(car.ev_soc, default=-1.0)
                    if raw >= 0:
                        soc_pct = raw
                    break

            power_w = self._get_ev_power_w(cfg.charger_power) if cfg.charger_power else 0.0

            result.append(ChargerState(
                config=cfg,
                connected=connected,
                active_car_name=active_car_name,
                current_a=self._get_state_float(cfg.charger_current),
                power_w=power_w,
                soc_pct=soc_pct,
            ))

        return result

    def _get_house_load_w(self, grid_l1, grid_l2, grid_l3, solar_w, battery_power_w, ev_total_w) -> float:
        house_entity = self._config.get(CONF_HOUSE_LOAD_ENTITY)
        if house_entity:
            val = self._get_state_float(house_entity)
            if val > 0:
                return val
        grid_total = grid_l1 + grid_l2 + grid_l3
        bat_charge    = max(0.0,  battery_power_w)
        bat_discharge = max(0.0, -battery_power_w)
        return max(0.0, grid_total + solar_w - bat_discharge + bat_charge - ev_total_w)

    # ── Bilval ────────────────────────────────────────────────────────

    def set_active_car(self, charger_name: str, car_name: str) -> None:
        """Anropas av select-entiteten när användaren väljer bil."""
        self._active_cars[charger_name] = car_name
        _LOGGER.info("Laddare '%s': bil vald → '%s'", charger_name, car_name)

    def get_active_car(self, charger_name: str) -> str:
        return self._active_cars.get(charger_name, NO_CAR_SELECTED)

    def get_charger_car_options(self, charger_name: str) -> list[str]:
        """Returnera lista av bilnamn för en given laddare + sentinel."""
        for ch_data in self._get_charger_configs():
            if ch_data.get("name") == charger_name:
                names = [car.get("name", "Bil") for car in ch_data.get("cars", [])]
                return [NO_CAR_SELECTED] + names
        return [NO_CAR_SELECTED]

    # ── Huvuduppdatering ──────────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        c = self._config
        try:
            spot_price    = self._get_nordpool_price()
            grid_fees     = float(c.get(CONF_GRID_FEES,         DEFAULT_GRID_FEES))
            energy_tax    = float(c.get(CONF_ENERGY_TAX,        DEFAULT_ENERGY_TAX))
            vat_rate      = float(c.get(CONF_VAT_RATE,          DEFAULT_VAT_RATE))
            extra_revenue = float(c.get(CONF_SELL_EXTRA_REVENUE, DEFAULT_SELL_EXTRA_REVENUE))

            buy_price  = self._controller.calculate_buy_price(spot_price, grid_fees, energy_tax, vat_rate)
            sell_price = self._controller.calculate_sell_price(spot_price, extra_revenue)

            grid_l1 = self._get_grid_power_w(c.get(CONF_GRID_POWER_L1))
            grid_l2 = self._get_grid_power_w(c.get(CONF_GRID_POWER_L2))
            grid_l3 = self._get_grid_power_w(c.get(CONF_GRID_POWER_L3))

            solar_w       = self._get_state_float(c.get(CONF_SOLAR_INVERTER_TOTAL))
            battery_pwr_w = self._get_state_float(c.get(CONF_BATTERY_INVERTER_POWER))
            chargers      = self._build_charger_states()

            ev_total_w = sum(ch.power_w for ch in chargers)
            house_load_w = self._get_house_load_w(
                grid_l1, grid_l2, grid_l3, solar_w, battery_pwr_w, ev_total_w
            )
            solar_surplus_w = max(0.0, solar_w - house_load_w)

            # Legionella – läs switch och temp
            now = datetime.now().astimezone()
            legionella_switch_on = self._get_state_bool(c.get(CONF_LEGIONELLA_SWITCH))
            hot_water_temp = self._get_hot_water_temp()
            legionella_active, legionella_reason = self._legionella.should_run_now(
                now, solar_surplus_w, buy_price,
                switch_is_on=legionella_switch_on,
                water_temp=hot_water_temp,
            )

            state = EnergyState(
                solar_power_w=solar_w,
                solar_power_l1=self._get_state_float(c.get(CONF_SOLAR_INVERTER_POWER_L1)),
                solar_power_l2=self._get_state_float(c.get(CONF_SOLAR_INVERTER_POWER_L2)),
                solar_power_l3=self._get_state_float(c.get(CONF_SOLAR_INVERTER_POWER_L3)),
                solar_forecast_today_kwh=self._get_state_float(c.get(CONF_SOLCAST_TODAY)),
                solar_forecast_tomorrow_kwh=self._get_state_float(c.get(CONF_SOLCAST_TOMORROW)),

                battery_soc_pct=self._get_state_float(c.get(CONF_BATTERY_SOC), default=50.0),
                battery_power_w=battery_pwr_w,
                battery_capacity_kwh=float(c.get(CONF_BATTERY_CAPACITY_KWH, 10.0)),
                battery_max_power_kw=float(c.get(CONF_BATTERY_MAX_POWER_KW, 5.0)),

                chargers=chargers,

                heat_pump_power_w=self._get_state_float(c.get(CONF_HEAT_PUMP_POWER)),
                heat_pump_on=self._get_state_bool(c.get(CONF_HEAT_PUMP_SWITCH)),
                heat_pump_phase=c.get(CONF_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PHASE),
                extra_hot_water_on=self._get_state_bool(c.get(CONF_HEAT_PUMP_EXTRA_HOT_WATER)),
                heat_pump_patron_phases=c.get(CONF_HEAT_PUMP_PATRON_PHASES, DEFAULT_HEAT_PUMP_PATRON_PHASES),
                heat_pump_patron_power_kw=float(c.get(CONF_HEAT_PUMP_PATRON_POWER_KW, DEFAULT_HEAT_PUMP_PATRON_POWER_KW)),

                legionella_active=legionella_active,

                grid_power_l1=grid_l1,
                grid_power_l2=grid_l2,
                grid_power_l3=grid_l3,
                grid_current_l1=self._get_state_float(c.get(CONF_GRID_CURRENT_L1)),
                grid_current_l2=self._get_state_float(c.get(CONF_GRID_CURRENT_L2)),
                grid_current_l3=self._get_state_float(c.get(CONF_GRID_CURRENT_L3)),

                house_load_w=house_load_w,
                hot_water_temp_c=hot_water_temp,
                extra_hot_water_max_temp=float(c.get(CONF_EXTRA_HOT_WATER_MAX_TEMP, DEFAULT_EXTRA_HOT_WATER_MAX_TEMP)),

                spot_price_sek_kwh=spot_price,
                buy_price_sek_kwh=buy_price,
                sell_price_sek_kwh=sell_price,

                operating_mode=self.operating_mode,
                winter_mode=bool(c.get(CONF_WINTER_MODE_ENABLED, False)),
            )
            self._state = state

            decision = self._controller.compute(state)
            self._last_decision = decision

            if legionella_active:
                decision.reason = legionella_reason + " | " + decision.reason

            # Skicka HA-notifieringar för laddare utan bilval
            if decision.chargers_needing_selection:
                await self._notify_car_selection_needed(decision.chargers_needing_selection)

            await self._execute_decision(state, decision)

            return {
                "state": state,
                "decision": decision,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "spot_price": spot_price,
                "house_load_w": house_load_w,
                "solar_surplus_w": solar_surplus_w,
                "legionella_active": legionella_active,
                "legionella_last_run": self._legionella.last_run,
                "legionella_days_since": self._legionella.days_since_last_run,
                "legionella_next_due": self._legionella.next_due(),
                "chargers_needing_selection": decision.chargers_needing_selection,
            }

        except Exception as err:
            _LOGGER.exception("Fel vid uppdatering av Smart Energy Manager")
            raise UpdateFailed(f"Error updating Smart Energy Manager: {err}") from err

    async def _notify_car_selection_needed(self, charger_names: list[str]) -> None:
        """Skicka persistent HA-notifiering för laddare som behöver bilval."""
        for name in charger_names:
            notification_id = f"sem_car_selection_{name.lower().replace(' ', '_')}"
            await self.hass.services.async_call(
                "persistent_notification", "create",
                {
                    "title": f"⚡ Smart Energy Manager – Välj bil",
                    "message": (
                        f"Laddare **{name}** är ansluten men ingen bil är vald.\n\n"
                        f"Välj bil i entiteten `select.sem_charger_{name.lower().replace(' ', '_')}_active_car` "
                        f"för att starta laddning."
                    ),
                    "notification_id": notification_id,
                },
                blocking=False,
            )

    async def _execute_decision(self, state: EnergyState, decision: ControlDecision) -> None:
        charge_entity    = self._config.get(CONF_BATTERY_INVERTER_CHARGE)
        discharge_entity = self._config.get(CONF_BATTERY_INVERTER_DISCHARGE)

        if charge_entity:
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": charge_entity, "value": round(decision.battery_charge_power_w)},
                blocking=False,
            )
        if discharge_entity:
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": discharge_entity, "value": round(decision.battery_discharge_power_w)},
                blocking=False,
            )

        for ch_state, ch_dec in zip(state.chargers, decision.charger_decisions):
            cfg = ch_state.config
            if ch_dec.enable and ch_dec.current_a > 0 and cfg.charger_current:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": cfg.charger_current, "value": round(ch_dec.current_a)},
                    blocking=False,
                )
            if cfg.charger_switch:
                service = "turn_on" if ch_dec.enable else "turn_off"
                await self.hass.services.async_call(
                    "switch", service,
                    {"entity_id": cfg.charger_switch},
                    blocking=False,
                )

        # Stäng av notifiering för laddare som inte längre behöver bilval
        for ch_state in state.chargers:
            if ch_state.config.name not in decision.chargers_needing_selection:
                notification_id = f"sem_car_selection_{ch_state.config.name.lower().replace(' ', '_')}"
                await self.hass.services.async_call(
                    "persistent_notification", "dismiss",
                    {"notification_id": notification_id},
                    blocking=False,
                )

        # Extra varmvatten (elpatron) – styrs av styrlogik
        hot_water_entity = self._config.get(CONF_HEAT_PUMP_EXTRA_HOT_WATER)
        if hot_water_entity:
            service = "turn_on" if decision.extra_hot_water else "turn_off"
            await self.hass.services.async_call(
                "switch", service,
                {"entity_id": hot_water_entity},
                blocking=False,
            )

        # Legionella-switch – separat switch som pannan äger av-sidan
        # Vi slår bara PÅ; pannan slår AV när programmet är klart.
        legionella_switch = self._config.get(CONF_LEGIONELLA_SWITCH)
        if legionella_switch and state.legionella_active:
            # Kontrollera om switchen redan är på för att undvika onödiga anrop
            current = self.hass.states.get(legionella_switch)
            if current and current.state != "on":
                await self.hass.services.async_call(
                    "switch", "turn_on",
                    {"entity_id": legionella_switch},
                    blocking=False,
                )

    def _get_hot_water_temp(self) -> Optional[float]:
        """Läs ackumulatortankens temperatur. Returnerar None om ingen sensor konfigurerad."""
        entity_id = self._config.get(CONF_HOT_WATER_TEMP_ENTITY)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    # ── Properties ────────────────────────────────────────────────────

    @property
    def last_decision(self) -> Optional[ControlDecision]:
        return self._last_decision

    @property
    def current_state(self) -> Optional[EnergyState]:
        return self._state

    @property
    def legionella(self) -> LegionellaManager:
        return self._legionella
