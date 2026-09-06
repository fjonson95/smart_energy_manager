"""Data coordinator for Smart Energy Manager."""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HOT_WATER_TEMP_ENTITY, CONF_LEGIONELLA_SWITCH,
    CONF_EXTRA_HOT_WATER_MAX_TEMP, CONF_EXTRA_HOT_WATER_MIN_TEMP, CONF_LEGIONELLA_TARGET_TEMP,
    CONF_EXTRA_HOT_WATER_MIN_RUNTIME_MINUTES,
    DEFAULT_EXTRA_HOT_WATER_MAX_TEMP, DEFAULT_EXTRA_HOT_WATER_MIN_TEMP, DEFAULT_LEGIONELLA_TARGET_TEMP,
    DEFAULT_EXTRA_HOT_WATER_MIN_RUNTIME_MINUTES,
    DOMAIN, UPDATE_INTERVAL,
    CONF_BATTERY_SOC, CONF_BATTERY_INVERTER_CHARGE, CONF_BATTERY_INVERTER_DISCHARGE,
    CONF_BATTERY_OPERATING_MODE_ENTITY,
    CONF_BATTERY_INVERTER_POWER, CONF_BATTERY_CAPACITY_KWH, CONF_BATTERY_MAX_POWER_KW,
    CONF_SOLAR_INVERTER_TOTAL,
    CONF_SOLAR_INVERTER_POWER_L1, CONF_SOLAR_INVERTER_POWER_L2, CONF_SOLAR_INVERTER_POWER_L3,
    CONF_EV_CHARGERS, CONF_EV_CARS,
    CONF_HEAT_PUMP_POWER, CONF_HEAT_PUMP_EXTRA_HOT_WATER,
    CONF_HEAT_PUMP_PHASE, CONF_HEAT_PUMP_PATRON_PHASES, CONF_HEAT_PUMP_PATRON_POWER_KW,
    CONF_GRID_POWER_L1, CONF_GRID_POWER_L2, CONF_GRID_POWER_L3,
    CONF_GRID_CURRENT_L1, CONF_GRID_CURRENT_L2, CONF_GRID_CURRENT_L3,
    CONF_NORDPOOL_ENTITY, CONF_NORDPOOL_TYPE, NORDPOOL_TYPE_HACS, NORDPOOL_TYPE_OFFICIAL,
    CONF_NORDPOOL_AREA, DEFAULT_NORDPOOL_AREA,
    CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW, CONF_ACTUAL_SOLAR_DAILY_ENTITY,
    CONF_GRID_FEES, CONF_ENERGY_TAX, CONF_VAT_RATE, CONF_SELL_EXTRA_REVENUE,
    CONF_MAX_CURRENT_PER_PHASE, CONF_GRID_VOLTAGE, CONF_MAX_EXPORT_W, DEFAULT_MAX_EXPORT_W,
    CONF_BATTERY_MIN_SOC, CONF_BATTERY_MAX_SOC,
    CONF_HOUSE_LOAD_ENTITY, CONF_GRID_POWER_UNIT, CONF_EV_POWER_UNIT,
    UNIT_W, UNIT_KW,
    DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE, DEFAULT_VAT_RATE,
    DEFAULT_GRID_FEES, DEFAULT_ENERGY_TAX, DEFAULT_SELL_EXTRA_REVENUE,
    DEFAULT_BATTERY_MIN_SOC, DEFAULT_BATTERY_MAX_SOC,
    DEFAULT_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PATRON_PHASES, DEFAULT_HEAT_PUMP_PATRON_POWER_KW,
    CHARGER_CONNECTED_STATES, NO_CAR_SELECTED,
    CONF_YESTERDAY_CONSUMPTION_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_HEAT_BALANCE_TEMP, CONF_HEAT_FACTOR_KWH_DD, CONF_BASE_DHW_KWH,
    CONF_DISINFECTING_EXTRA_KWH,
    DEFAULT_HEAT_BALANCE_TEMP, DEFAULT_HEAT_FACTOR_KWH_DD, DEFAULT_BASE_DHW_KWH,
    DEFAULT_DISINFECTING_EXTRA_KWH,
    CONF_EXPORT_SELL_PERCENTILE, CONF_EXPORT_MIN_SOLAR_TOMORROW_KWH, CONF_BATTERY_POWER_INVERTED,
    CONF_EXPORT_MIN_SELL_PRICE_SEK_KWH, DEFAULT_EXPORT_MIN_SELL_PRICE_SEK_KWH,
    DEFAULT_EXPORT_SELL_PERCENTILE, DEFAULT_EXPORT_MIN_SOLAR_TOMORROW_KWH,
    CONF_ETA_ROUNDTRIP, DEFAULT_ETA_ROUNDTRIP, CONF_CYCLE_COST_SEK_KWH, DEFAULT_CYCLE_COST_SEK_KWH,
    MODE_AUTO, MODE_MANUAL,
)
from .price_scheduler import PriceScheduler
from .energy_controller import (
    EnergyController, EnergyState, ControlDecision,
    ChargerConfig, CarConfig, ChargerState,
)
from .energy_planner import EnergyPlanner, DayPlan
from .legionella import LegionellaManager

_LOGGER = logging.getLogger(__name__)

# P6-1: dödband på tjänsteanrop – skriv bara vid förändring, men aldrig glesare
# än HEARTBEAT_INTERVAL (självläkning om en skrivning tappas bort, P0-2).
_HEARTBEAT_INTERVAL = timedelta(minutes=5)
_POWER_DEADBAND_W = 50.0

# Kvällsmålets huslast-term (_auto_mode) projicerar en momentan avläsning över
# hela mörkerperioden (timmar) - ett par minuters kokplatta/dusch/ugn räknas
# annars som "detta är den nya normala nattförbrukningen" och skjuter
# kvällsmålets SOC över batteriets faktiska nivå, vilket stänger av
# egenförbrukningsurladdningen tills toppen klingar av. Ett glidande medel
# över EVENING_LOAD_AVG_WINDOW dämpar just den tillfälliga toppen utan att
# göra huslasten trögare någon annanstans (cover_load m.fl. använder
# fortfarande den snabba, ofiltrerade house_load_w).
_EVENING_LOAD_AVG_WINDOW = timedelta(minutes=15)


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
        self._price_scheduler = PriceScheduler(
            grid_fees_sek=float(self._config.get(CONF_GRID_FEES, DEFAULT_GRID_FEES)),
            energy_tax_sek=float(self._config.get(CONF_ENERGY_TAX, DEFAULT_ENERGY_TAX)),
            vat_rate=float(self._config.get(CONF_VAT_RATE, DEFAULT_VAT_RATE)),
            extra_revenue_sek=float(self._config.get(CONF_SELL_EXTRA_REVENUE, DEFAULT_SELL_EXTRA_REVENUE)),
        )

        self._grid_scale = 1000.0 if self._config.get(CONF_GRID_POWER_UNIT, UNIT_W) == UNIT_KW else 1.0
        self._ev_scale   = 1000.0 if self._config.get(CONF_EV_POWER_UNIT,   UNIT_W) == UNIT_KW else 1.0

        # Dag-framåt planerare (parallellt med EnergyController – påverkar inga beslut)
        self._energy_planner = EnergyPlanner(
            battery_min_soc=float(self._config.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)),
            battery_max_soc=float(self._config.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC)),
            export_sell_percentile=float(self._config.get(CONF_EXPORT_SELL_PERCENTILE, DEFAULT_EXPORT_SELL_PERCENTILE)),
            export_min_sell_price_sek_kwh=float(self._config.get(CONF_EXPORT_MIN_SELL_PRICE_SEK_KWH, DEFAULT_EXPORT_MIN_SELL_PRICE_SEK_KWH)),
            export_min_solar_tomorrow_kwh=float(self._config.get(CONF_EXPORT_MIN_SOLAR_TOMORROW_KWH, DEFAULT_EXPORT_MIN_SOLAR_TOMORROW_KWH)),
            eta_roundtrip=float(self._config.get(CONF_ETA_ROUNDTRIP, DEFAULT_ETA_ROUNDTRIP)),
            cycle_cost_sek_kwh=float(self._config.get(CONF_CYCLE_COST_SEK_KWH, DEFAULT_CYCLE_COST_SEK_KWH)),
        )
        self._day_plan: Optional[DayPlan] = None
        self._last_plan_ps_sig: tuple = (0, None, None)

        # active_car[charger_name] = car_name eller NO_CAR_SELECTED
        # Styrs av select-entiteten i select.py
        self._active_cars: dict[str, str] = {}
        self._init_active_cars()

        # Vakthund: när laddning beordrades (bil vald) men ingen effekt syns ännu
        # charger_name → tidpunkt då laddning startades utan ström
        self._charge_command_times: dict[str, datetime] = {}

        # Rullande median (3-5 sampel) av house_load_entity – dämpar mätarglapp
        # när batteriet byter laddningsriktning.
        self._house_load_samples: list[float] = []

        # Separat, längre glidande medel (se _EVENING_LOAD_AVG_WINDOW) – bara för
        # kvällsmålets huslast-projektion i _auto_mode(), inte för house_load_w
        # i övrigt.
        self._house_load_long_samples: list[tuple[datetime, float]] = []

        # Rullande temperaturmedelvärde för förbrukningsprognos
        # Modellen är kalibrerad mot dygnsmedeltemperatur, inte ögonblicksvärde
        self._temp_samples: list[float] = []
        self._temp_sample_date: str = ""
        self._yesterday_avg_temp: Optional[float] = None

        # Sparad solar_takeover_dt: beräknas live men sparas undan så att
        # inaktuell Solcast-data inte nollställer värdet mitt i natten.
        # Nollställs när sol producerar igen (solar_w > 200 W).
        self._saved_solar_takeover_dt: Optional[datetime] = None
        self.opportunistic_charge_enabled: bool = True

        # Minimitid-spärr för extra varmvatten (förhindrar flimmer på/av)
        self._extra_hot_water_started_at: Optional[datetime] = None
        self._extra_hot_water_actual_state: bool = False

        # Registreras av BatteryAccumulatedCostSensor för att möjliggöra reset via service
        self._battery_cost_reset_cb = None
        self._battery_avg_cost_sek_kwh: float = 0.0

        # Cache för officiell Nordpool-integration (uppdateras max en gång per dag)
        self._official_nordpool_cache: dict = {}

        # Observerad solar-takeover: när solöverskottet hållit sig > 0 i ≥15 min
        # Sparas i HA Store så att värdet överlever omstarter.
        self._takeover_store = Store(hass, 1, f"{DOMAIN}_solar_takeover")
        self._observed_takeover_minutes: list[float] = []   # senaste 14 obs (min fr midnatt)
        self._surplus_positive_since: Optional[datetime] = None
        self._takeover_observed_today: bool = False

        # Varning om batteriets egna driftläge inte står på "manual" – då är
        # alla börvärden SEM skriver verkningslösa utan att något syns.
        self._battery_mode_warning_active: bool = False

        # Produktionskvot (P3-2): rullande 3-dygns kvot faktisk/prognos
        # solproduktion – upptäcker snötäckta/nedsmutsade paneler som
        # Solcast-prognosen inte känner till. Sparas i HA Store.
        self._pv_ratio_store = Store(hass, 1, f"{DOMAIN}_pv_ratio")
        self._pv_ratio_history: list[dict] = []   # senaste 3 dygn: [{date, actual_kwh, forecast_kwh}]
        self._pv_ratio_date: str = ""
        self._pv_today_forecast_kwh: Optional[float] = None
        self._pv_last_actual_reading: float = 0.0

        # P6-1: senaste skrivningstidpunkt per entitet, för dödband + heartbeat.
        self._last_write_times: dict[str, datetime] = {}

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
        await self._load_takeover_store()
        await self._load_pv_ratio_store()
        await super().async_config_entry_first_refresh()

    async def _load_pv_ratio_store(self) -> None:
        data = await self._pv_ratio_store.async_load()
        if isinstance(data, dict):
            self._pv_ratio_history = data.get("history", [])
            self._pv_today_forecast_kwh = data.get("today_forecast_kwh")
            self._pv_ratio_date = data.get("ratio_date", "")

    async def _save_pv_ratio_store(self) -> None:
        await self._pv_ratio_store.async_save({
            "history": self._pv_ratio_history,
            "today_forecast_kwh": self._pv_today_forecast_kwh,
            "ratio_date": self._pv_ratio_date,
        })

    def _update_pv_production_ratio(self, now: datetime, solar_forecast_today_kwh: float) -> float:
        """P3-2: rullande 3-dygns kvot faktisk/prognos solproduktion.

        Källor: actual_solar_daily_entity (nollställs vid midnatt) mot Solcasts
        prognos för samma dygn, fångad EN gång tidigt på dygnet (innan
        produktion hunnit äta av "remaining"-värdet). Kvot < 0,6 → golvet
        (build_plan) höjs mot full nattautonomi via _uncertainty_markup().
        """
        actual_entity = self._config.get(CONF_ACTUAL_SOLAR_DAILY_ENTITY)
        if not actual_entity:
            return 1.0

        current_actual = self._get_state_float(actual_entity)
        today_str = now.strftime("%Y-%m-%d")

        if today_str != self._pv_ratio_date:
            # Nytt dygn – gårdagens slutvärde är det SENAST SAMPLADE (från
            # föregående cykel), eftersom current_actual redan kan ha
            # nollställts för det nya dygnet av källsensorn.
            if (
                self._pv_ratio_date
                and self._pv_today_forecast_kwh is not None
                and self._pv_last_actual_reading > 0
            ):
                self._pv_ratio_history.append({
                    "date": self._pv_ratio_date,
                    "actual_kwh": self._pv_last_actual_reading,
                    "forecast_kwh": self._pv_today_forecast_kwh,
                })
                self._pv_ratio_history = self._pv_ratio_history[-3:]
                self.hass.async_create_task(self._save_pv_ratio_store())
                _LOGGER.info(
                    "Produktionskvot: %s faktisk=%.1f kWh prognos=%.1f kWh",
                    self._pv_ratio_date, self._pv_last_actual_reading, self._pv_today_forecast_kwh,
                )
            self._pv_ratio_date = today_str
            self._pv_today_forecast_kwh = None

        if self._pv_today_forecast_kwh is None and solar_forecast_today_kwh > 0:
            self._pv_today_forecast_kwh = solar_forecast_today_kwh
            self.hass.async_create_task(self._save_pv_ratio_store())

        self._pv_last_actual_reading = current_actual

        if not self._pv_ratio_history:
            return 1.0
        total_actual = sum(h["actual_kwh"] for h in self._pv_ratio_history)
        total_forecast = sum(h["forecast_kwh"] for h in self._pv_ratio_history)
        return total_actual / total_forecast if total_forecast > 0 else 1.0

    async def _load_takeover_store(self) -> None:
        data = await self._takeover_store.async_load()
        if isinstance(data, dict):
            obs = data.get("observations", [])
            self._observed_takeover_minutes = [float(v) for v in obs if isinstance(v, (int, float))]
            last_date = data.get("last_obs_date", "")
            if last_date == dt_util.now().strftime("%Y-%m-%d"):
                self._takeover_observed_today = True
        elif isinstance(data, list):
            self._observed_takeover_minutes = [float(v) for v in data if isinstance(v, (int, float))]

    async def _save_takeover_store(self) -> None:
        await self._takeover_store.async_save({
            "observations": self._observed_takeover_minutes,
            "last_obs_date": dt_util.now().strftime("%Y-%m-%d"),
        })

    def _weighted_observed_minutes(self) -> Optional[float]:
        obs = self._observed_takeover_minutes
        if not obs:
            return None
        weights = list(range(1, len(obs) + 1))
        return sum(w * m for w, m in zip(weights, obs)) / sum(weights)

    def _blend_takeover_dt(
        self,
        solcast_dt: Optional[datetime],
        now_utc: datetime,
    ) -> Optional[datetime]:
        """Viktad blend 60% Solcast + 40% historisk observation (minuter fr midnatt)."""
        obs_min = self._weighted_observed_minutes()

        if solcast_dt is None and obs_min is None:
            return None

        # Konvertera Solcast till minuter från midnatt lokal tid
        if solcast_dt is not None:
            local = solcast_dt.astimezone()
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            solcast_min = (local - midnight).total_seconds() / 60.0
        else:
            solcast_min = None

        if solcast_min is not None and obs_min is not None:
            blended_min = 0.6 * solcast_min + 0.4 * obs_min
        elif solcast_min is not None:
            blended_min = solcast_min
        else:
            blended_min = obs_min

        # Bygg datetime för imorgon (om blended_min är i det förflutna idag) eller idag
        local_now = dt_util.now()
        midnight_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        candidate = midnight_today + timedelta(minutes=blended_min)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate

    def _build_controller(self) -> EnergyController:
        c = self._config
        return EnergyController(
            max_current_per_phase=float(c.get(CONF_MAX_CURRENT_PER_PHASE, DEFAULT_MAX_CURRENT)),
            grid_voltage=float(c.get(CONF_GRID_VOLTAGE, DEFAULT_GRID_VOLTAGE)),
            max_export_w=float(c.get(CONF_MAX_EXPORT_W, DEFAULT_MAX_EXPORT_W)),
            battery_min_soc=float(c.get(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)),
            battery_max_soc=float(c.get(CONF_BATTERY_MAX_SOC, DEFAULT_BATTERY_MAX_SOC)),
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

    def _get_state_tristate(self, entity_id: Optional[str]) -> Optional[bool]:
        """Som _get_state_bool, men returnerar None vid unavailable/unknown istället
        för att tolka det som av. Används där ett tillfälligt kommunikationsglapp
        inte får misstolkas som en riktig av-övergång (t.ex. legionella-switchen)."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        return state.state.lower() in ("on", "true", "1", "home", "charging")

    def _get_sun_datetime(self, attribute: str) -> Optional[datetime]:
        """Läs sol-tidpunkt från sun.sun-entitetens attribut."""
        sun_state = self.hass.states.get("sun.sun")
        if sun_state is None:
            return None
        raw = sun_state.attributes.get(attribute)
        if raw is None:
            return None
        try:
            if isinstance(raw, datetime):
                return raw
            dt = datetime.fromisoformat(str(raw))
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return dt
        except (ValueError, TypeError):
            return None

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

    def _nordpool_price_in_cents(self, state_attributes: dict) -> bool:
        """Returnera True om Nordpool-sensorn rapporterar i öre (HACS) istället för kr (officiell)."""
        nordpool_type = self._config.get(CONF_NORDPOOL_TYPE, NORDPOOL_TYPE_HACS)
        if nordpool_type == NORDPOOL_TYPE_OFFICIAL:
            return False
        # HACS: läs price_in_cents från attribut, fallback True
        return bool(state_attributes.get("price_in_cents", True))

    def _get_nordpool_price(self) -> float:
        """Läs Nordpool spotpris och returnera i SEK/kWh."""
        entity_id = self._config.get(CONF_NORDPOOL_ENTITY)
        if not entity_id:
            return 0.0
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return 0.0
        try:
            raw = float(state.state)
            return raw / 100.0 if self._nordpool_price_in_cents(state.attributes) else raw
        except (ValueError, TypeError):
            return 0.0

    def _build_charger_states(self) -> list[ChargerState]:
        charger_cfgs = self._get_charger_configs()
        result: list[ChargerState] = []

        for i, ch_data in enumerate(charger_cfgs):
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

            # Rensa bilval automatiskt när SOC-mål är uppnått
            if active_car_name != NO_CAR_SELECTED and soc_pct is not None:
                for car in cars:
                    if car.name == active_car_name and soc_pct >= car.ev_soc_target:
                        _LOGGER.info(
                            "Laddare '%s': %s nådde SOC-mål %.0f%% – återställer bilval",
                            cfg.name, active_car_name, soc_pct,
                        )
                        self._active_cars[cfg.name] = NO_CAR_SELECTED
                        active_car_name = NO_CAR_SELECTED
                        soc_pct = None
                        break

            power_w = self._get_ev_power_w(cfg.charger_power) if cfg.charger_power else 0.0

            # Vakthund: bil vald + laddning beordrad men ingen effekt inom 5 min → rensa bilval
            if active_car_name != NO_CAR_SELECTED and connected:
                prev_decision = self._last_decision
                prev_charger_enabled = (
                    prev_decision is not None
                    and i < len(prev_decision.charger_decisions)
                    and prev_decision.charger_decisions[i].enable
                )
                if prev_charger_enabled and power_w < 50.0:
                    if cfg.name not in self._charge_command_times:
                        self._charge_command_times[cfg.name] = dt_util.now()
                    elif (dt_util.now() - self._charge_command_times[cfg.name]).total_seconds() >= 300:
                        _LOGGER.warning(
                            "Laddare '%s': laddning beordrad i >5 min utan ström (%.0f W) – återställer bilval",
                            cfg.name, power_w,
                        )
                        self._active_cars[cfg.name] = NO_CAR_SELECTED
                        active_car_name = NO_CAR_SELECTED
                        soc_pct = None
                        self._charge_command_times.pop(cfg.name, None)
                else:
                    self._charge_command_times.pop(cfg.name, None)
            else:
                self._charge_command_times.pop(cfg.name, None)

            result.append(ChargerState(
                config=cfg,
                connected=connected,
                active_car_name=active_car_name,
                current_a=self._get_state_float(cfg.charger_current),
                power_w=power_w,
                soc_pct=soc_pct,
            ))

        return result

    def _get_house_load_w(self, grid_l1, grid_l2, grid_l3, solar_w, battery_power_w, ev_total_w) -> tuple[float, bool]:
        """Returnerar (huslast_w, from_sensor). from_sensor=True innebär att en
        riktig mätarläsning (median-filtrerad) användes – golvskyddet mot gårdagens
        snitt ska då INTE tillämpas, bara när vi föll tillbaka på energibalansformeln."""
        house_entity = self._config.get(CONF_HOUSE_LOAD_ENTITY)
        if house_entity:
            val = self._get_state_float(house_entity, default=-1.0)
            if val > 0:
                self._house_load_samples.append(val)
                if len(self._house_load_samples) > 5:
                    self._house_load_samples = self._house_load_samples[-5:]
                return statistics.median(self._house_load_samples), True
        grid_total = grid_l1 + grid_l2 + grid_l3
        bat_charge    = max(0.0,  battery_power_w)
        bat_discharge = max(0.0, -battery_power_w)
        return max(0.0, grid_total + solar_w - bat_discharge + bat_charge - ev_total_w), False

    def _get_house_load_avg_w(self, house_load_w: float, now: datetime) -> float:
        """Glidande medel av huslasten över _EVENING_LOAD_AVG_WINDOW – se
        konstantens kommentar för varför kvällsmålet behöver ett trögare
        underlag än den snabba house_load_w."""
        self._house_load_long_samples.append((now, house_load_w))
        cutoff = now - _EVENING_LOAD_AVG_WINDOW
        self._house_load_long_samples = [
            (ts, v) for ts, v in self._house_load_long_samples if ts >= cutoff
        ]
        values = [v for _, v in self._house_load_long_samples]
        return statistics.fmean(values) if values else house_load_w

    # ── Bilval ────────────────────────────────────────────────────────

    def reset_battery_cost(self) -> None:
        """Nollställ batterikostnadsackumulatorn via service-anrop."""
        if self._battery_cost_reset_cb:
            self._battery_cost_reset_cb()

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

    # ── Officiell Nordpool-integration ───────────────────────────────

    async def _fetch_official_nordpool_slots(self) -> dict | None:
        """Hämta prisslots från officiella Nordpool-integrationen via service-anrop.

        Returnerar dict med today/tomorrow-listor i same format som HACS raw_today/raw_tomorrow
        (fältet heter "value" i SEK/kWh). Cachar resultatet per dag.
        """
        entries = self.hass.config_entries.async_entries("nordpool")
        if not entries:
            _LOGGER.warning("Officiell Nordpool-integration ej hittad i HA")
            return None

        entry_id = entries[0].entry_id
        area = self._config.get(CONF_NORDPOOL_AREA, DEFAULT_NORDPOOL_AREA)
        today = dt_util.now().date()
        today_str = today.isoformat()
        tomorrow_str = (today + timedelta(days=1)).isoformat()

        def _convert(slots: list) -> list:
            return [
                {"start": s["start"], "end": s["end"], "value": s["price"] / 1000}
                for s in slots
            ]

        cached = self._official_nordpool_cache
        if cached.get("date") == today_str and cached.get("today"):
            if not cached.get("tomorrow"):
                try:
                    resp = await self.hass.services.async_call(
                        "nordpool", "get_prices_for_date",
                        {"config_entry": entry_id, "date": tomorrow_str},
                        blocking=True, return_response=True,
                    )
                    slots = next(iter((resp or {}).values()), [])
                    if slots:
                        cached["tomorrow"] = _convert(slots)
                except Exception as exc:
                    _LOGGER.debug("Imorgondagens officiella Nordpool-priser ej tillgängliga: %s", exc)
            return cached

        try:
            resp_today = await self.hass.services.async_call(
                "nordpool", "get_prices_for_date",
                {"config_entry": entry_id, "date": today_str},
                blocking=True, return_response=True,
            )
            today_slots = next(iter((resp_today or {}).values()), [])

            resp_tomorrow = await self.hass.services.async_call(
                "nordpool", "get_prices_for_date",
                {"config_entry": entry_id, "date": tomorrow_str},
                blocking=True, return_response=True,
            )
            tomorrow_slots = next(iter((resp_tomorrow or {}).values()), [])

            self._official_nordpool_cache = {
                "date": today_str,
                "today": _convert(today_slots),
                "tomorrow": _convert(tomorrow_slots),
            }
            return self._official_nordpool_cache
        except Exception as exc:
            _LOGGER.warning("Fel vid hämtning av officiella Nordpool-priser: %s", exc)
            return None

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

            # Gårdagens förbrukning – behövs tidigt för huslastgolvet nedan
            yesterday_kwh: Optional[float] = None
            yest_entity = c.get(CONF_YESTERDAY_CONSUMPTION_ENTITY)
            if yest_entity:
                _yest_st = self.hass.states.get(yest_entity)
                if _yest_st:
                    _lp = _yest_st.attributes.get("last_period")
                    yesterday_kwh = float(_lp) if _lp is not None else None

            grid_l1 = self._get_grid_power_w(c.get(CONF_GRID_POWER_L1))
            grid_l2 = self._get_grid_power_w(c.get(CONF_GRID_POWER_L2))
            grid_l3 = self._get_grid_power_w(c.get(CONF_GRID_POWER_L3))

            solar_w       = self._get_state_float(c.get(CONF_SOLAR_INVERTER_TOTAL))
            _bat_raw      = self._get_state_float(c.get(CONF_BATTERY_INVERTER_POWER))
            battery_pwr_w = -_bat_raw if c.get(CONF_BATTERY_POWER_INVERTED, False) else _bat_raw
            chargers      = self._build_charger_states()

            ev_total_w = sum(ch.power_w for ch in chargers)
            house_load_w, _house_load_from_sensor = self._get_house_load_w(
                grid_l1, grid_l2, grid_l3, solar_w, battery_pwr_w, ev_total_w
            )
            # Golvskydd gäller BARA energibalansformeln (house_load_entity saknas/unavailable).
            # Den formeln har en känd bugg vid batteribyte (urladdning→laddning): grid-sensorn
            # visar export (-) i övergångscykeln → house_load ≈ 0W → solöverskott = hela
            # solproduktionen → batteriet ber om maxladdning från nätet. En riktig mätarläsning
            # (median-filtrerad ovan) ska aldrig klämmas mot gårdagens snitt – en genuin dipp
            # mitt på dagen är då legitim, inte en formelbugg.
            if not _house_load_from_sensor and solar_w > 200 and yesterday_kwh:
                _load_floor = yesterday_kwh / 24.0 * 1000.0
                if house_load_w < _load_floor:
                    _LOGGER.debug(
                        "Huslast %.0fW under golvet %.0fW (formelbuggar vid batteribyte) – korrigeras",
                        house_load_w, _load_floor,
                    )
                    house_load_w = _load_floor
            solar_surplus_w = max(0.0, solar_w - house_load_w)

            # Prisschema från Nordpool + Solcast-attributen
            now = dt_util.now()
            house_load_avg_w = self._get_house_load_avg_w(house_load_w, now)
            nordpool_entity = c.get(CONF_NORDPOOL_ENTITY)
            nordpool_type = c.get(CONF_NORDPOOL_TYPE, NORDPOOL_TYPE_HACS)
            price_schedule = None
            if nordpool_entity:
                try:
                    solcast_today_attrs = None
                    solcast_tomorrow_attrs = None
                    sc_today = c.get(CONF_SOLCAST_TODAY)
                    sc_tomorrow = c.get(CONF_SOLCAST_TOMORROW)
                    if sc_today:
                        st = self.hass.states.get(sc_today)
                        if st:
                            solcast_today_attrs = dict(st.attributes)
                    if sc_tomorrow:
                        st = self.hass.states.get(sc_tomorrow)
                        if st:
                            solcast_tomorrow_attrs = dict(st.attributes)

                    if nordpool_type == NORDPOOL_TYPE_OFFICIAL:
                        official_data = await self._fetch_official_nordpool_slots()
                        if official_data and official_data.get("today"):
                            synthetic_attrs = {
                                "raw_today": official_data["today"],
                                "raw_tomorrow": official_data.get("tomorrow", []),
                            }
                            price_schedule = self._price_scheduler.compute(
                                synthetic_attrs, now,
                                solcast_today_attrs=solcast_today_attrs,
                                solcast_tomorrow_attrs=solcast_tomorrow_attrs,
                                price_in_cents=False,
                            )
                    else:
                        nordpool_state = self.hass.states.get(nordpool_entity)
                        if nordpool_state and nordpool_state.attributes:
                            price_schedule = self._price_scheduler.compute(
                                nordpool_state.attributes, now,
                                solcast_today_attrs=solcast_today_attrs,
                                solcast_tomorrow_attrs=solcast_tomorrow_attrs,
                                price_in_cents=self._nordpool_price_in_cents(nordpool_state.attributes),
                            )
                except Exception as e:
                    _LOGGER.warning("Kunde inte beräkna prisschema: %s", e)

            # Tidpunkt då sol förväntas täcka huslasten.
            # Referenslast = gårdagens snitt (stabil, immun mot nattliga spikar).
            # Sök först i dagens prognos (framtida slots), sedan imorgons.
            # Sparar undan det senaste giltiga värdet så att inaktuell Solcast-data
            # inte nollställer takeover-tid mitt i natten.
            _ref_load_w = (yesterday_kwh / 24.0 * 1000.0) if yesterday_kwh else house_load_w
            solar_takeover_dt = None
            _now_for_takeover = datetime.now(timezone.utc)
            for _sc_key in (CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW):
                _sc_entity = c.get(_sc_key)
                if not _sc_entity:
                    continue
                _sc_st = self.hass.states.get(_sc_entity)
                if not _sc_st:
                    continue
                for slot in _sc_st.attributes.get("detailedForecast", []):
                    try:
                        slot_start = datetime.fromisoformat(slot["period_start"])
                        if slot_start.tzinfo is None:
                            slot_start = slot_start.replace(tzinfo=timezone.utc)
                        if slot_start <= _now_for_takeover:
                            continue
                        if slot.get("pv_estimate10", 0.0) * 1000.0 >= _ref_load_w:
                            solar_takeover_dt = slot_start
                            break
                    except Exception:
                        pass
                if solar_takeover_dt is not None:
                    break

            # Nollställ sparat värde när sol producerar (nytt dygn börjar)
            if solar_w > 200:
                self._saved_solar_takeover_dt = None

            if solar_takeover_dt is not None:
                self._saved_solar_takeover_dt = solar_takeover_dt
            elif self._saved_solar_takeover_dt is not None:
                # Solcast inaktuell – återanvänd senast kända takeover-tid
                solar_takeover_dt = self._saved_solar_takeover_dt
                _LOGGER.debug("solar_takeover_dt: Solcast inaktuell, återanvänder sparat värde %s", solar_takeover_dt)

            # Observera när solöverskott > 0 i ≥15 min – bygger historisk takeover-tid.
            # Nollställ vid midnatt (nytt dygn).
            _local_now = dt_util.now()
            _today_str = _local_now.strftime("%Y-%m-%d")
            if not hasattr(self, "_takeover_obs_date"):
                self._takeover_obs_date = _today_str
            if _today_str != self._takeover_obs_date:
                self._surplus_positive_since = None
                self._takeover_observed_today = False
                self._takeover_obs_date = _today_str
                if hasattr(self, "_had_negative_surplus_today"):
                    del self._had_negative_surplus_today

            if not self._takeover_observed_today:
                net_surplus = solar_w - house_load_w
                if net_surplus > 0:
                    if self._surplus_positive_since is None:
                        # Om surplus redan är positivt vid första cykeln efter uppstart
                        # (ingen tidigare negativ cykel sedd) vet vi inte när det startade –
                        # skippa dagens observation för att undvika felaktig tid.
                        if not hasattr(self, "_had_negative_surplus_today"):
                            self._takeover_observed_today = True
                            _LOGGER.debug(
                                "solar_takeover obs: surplus positiv vid uppstart – skippar dagens observation"
                            )
                        else:
                            self._surplus_positive_since = _local_now
                    elif (_local_now - self._surplus_positive_since).total_seconds() >= 900:
                        # 15 min sammanhängande surplus – spara starttiden
                        midnight = _local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                        obs_min = (self._surplus_positive_since - midnight).total_seconds() / 60.0
                        self._observed_takeover_minutes.append(obs_min)
                        if len(self._observed_takeover_minutes) > 14:
                            self._observed_takeover_minutes = self._observed_takeover_minutes[-14:]
                        self._takeover_observed_today = True
                        _LOGGER.info(
                            "Observerad solar takeover: %.0f min fr midnatt (kl %s)",
                            obs_min, self._surplus_positive_since.strftime("%H:%M"),
                        )
                        await self._save_takeover_store()
                else:
                    self._surplus_positive_since = None
                    self._had_negative_surplus_today = True

            # Viktad blend: 60% Solcast-prognos + 40% historisk observation
            solar_takeover_dt = self._blend_takeover_dt(solar_takeover_dt, _now_for_takeover)
            if solar_takeover_dt is None and (sun_rising := self._get_sun_datetime("next_rising")):
                solar_takeover_dt = sun_rising + timedelta(hours=3)
                _LOGGER.debug("solar_takeover_dt: fallback soluppgång+3h → %s", solar_takeover_dt)

            # Utomhustemperatur och förbrukningsprognos
            outdoor_temp: Optional[float] = None
            temp_entity = c.get(CONF_OUTDOOR_TEMP_ENTITY)
            if temp_entity:
                val = self._get_state_float(temp_entity)
                if val != 0.0 or self.hass.states.get(temp_entity) is not None:
                    outdoor_temp = val

            # Bygg upp rullande dygnsmedeltemperatur.
            # Modellen är kalibrerad mot dygnsmedeltemp, inte ögonblicksvärde.
            if outdoor_temp is not None:
                today_str = now.strftime("%Y-%m-%d")
                if today_str != self._temp_sample_date:
                    # Nytt dygn – lås in gårdagens medelvärde och nollställ
                    if self._temp_samples:
                        self._yesterday_avg_temp = sum(self._temp_samples) / len(self._temp_samples)
                        _LOGGER.debug(
                            "Temperaturmedel för %s: %.1f °C (%d mätningar)",
                            self._temp_sample_date, self._yesterday_avg_temp, len(self._temp_samples),
                        )
                    self._temp_samples = []
                    self._temp_sample_date = today_str
                self._temp_samples.append(outdoor_temp)

            # Välj temperaturindata: gårdagens medel om tillgängligt, annars aktuell
            temp_for_model = self._yesterday_avg_temp if self._yesterday_avg_temp is not None else outdoor_temp

            # Desinficering/legionella pågår? – återanvänd samma switch som legionella-fliken
            disinfecting_active = self._get_state_bool(c.get(CONF_LEGIONELLA_SWITCH))

            predicted_daily_kwh = 0.0
            if temp_for_model is not None:
                t_bal = float(c.get(CONF_HEAT_BALANCE_TEMP, DEFAULT_HEAT_BALANCE_TEMP))
                k     = float(c.get(CONF_HEAT_FACTOR_KWH_DD, DEFAULT_HEAT_FACTOR_KWH_DD))
                base  = float(c.get(CONF_BASE_DHW_KWH, DEFAULT_BASE_DHW_KWH))
                predicted_daily_kwh = base + k * max(0.0, t_bal - temp_for_model)
                if disinfecting_active:
                    extra = float(c.get(CONF_DISINFECTING_EXTRA_KWH, DEFAULT_DISINFECTING_EXTRA_KWH))
                    predicted_daily_kwh += extra

            # Legionella – läs switch (tri-state: unavailable ska INTE tolkas som av,
            # annars misstolkas ett kort kommunikationsglapp mot ems-esp som att
            # pannan avslutat körningen) och temp
            legionella_switch_on = self._get_state_tristate(c.get(CONF_LEGIONELLA_SWITCH))
            hot_water_temp = self._get_hot_water_temp()
            legionella_active, legionella_reason = self._legionella.should_run_now(
                now, solar_surplus_w, buy_price,
                switch_is_on=legionella_switch_on,
                water_temp=hot_water_temp,
                price_schedule=price_schedule,
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
                house_load_avg_w=house_load_avg_w,
                hot_water_temp_c=hot_water_temp,
                extra_hot_water_max_temp=float(c.get(CONF_EXTRA_HOT_WATER_MAX_TEMP, DEFAULT_EXTRA_HOT_WATER_MAX_TEMP)),
                extra_hot_water_min_temp=float(c.get(CONF_EXTRA_HOT_WATER_MIN_TEMP, DEFAULT_EXTRA_HOT_WATER_MIN_TEMP)),

                spot_price_sek_kwh=spot_price,
                buy_price_sek_kwh=buy_price,
                sell_price_sek_kwh=sell_price,

                operating_mode=self.operating_mode,
                now=now,
                price_schedule=price_schedule,
                yesterday_consumption_kwh=yesterday_kwh,
                outdoor_temp_c=outdoor_temp,
                avg_temp_yesterday_c=self._yesterday_avg_temp,
                disinfecting_active=disinfecting_active,
                predicted_daily_kwh=predicted_daily_kwh,
                sun_next_setting=self._get_sun_datetime("next_setting"),
                sun_next_rising=self._get_sun_datetime("next_rising"),
                solar_takeover_dt=solar_takeover_dt,
                battery_avg_cost_sek_kwh=self._battery_avg_cost_sek_kwh,
                opportunistic_charge_enabled=self.opportunistic_charge_enabled,
            )
            self._state = state

            pv_production_ratio = self._update_pv_production_ratio(now, state.solar_forecast_today_kwh)

            # ── Dag-framåt plan ────────────────────────────────────────────
            if price_schedule:
                ps_sig = (
                    len(price_schedule.slots),
                    price_schedule.slots[0].start if price_schedule.slots else None,
                    price_schedule.slots[-1].start if price_schedule.slots else None,
                )
                plan_expired = self._day_plan is None or now >= self._day_plan.valid_until
                if plan_expired or ps_sig != self._last_plan_ps_sig:
                    try:
                        self._day_plan = self._energy_planner.build_plan(
                            now=now,
                            battery_soc_pct=state.battery_soc_pct,
                            battery_capacity_kwh=state.battery_capacity_kwh,
                            battery_max_power_kw=state.battery_max_power_kw,
                            ps=price_schedule,
                            predicted_daily_kwh=state.predicted_daily_kwh,
                            solar_forecast_tomorrow_kwh=state.solar_forecast_tomorrow_kwh,
                            solar_takeover_dt=state.solar_takeover_dt,
                            house_load_w=state.house_load_w,
                            battery_avg_cost_sek_kwh=state.battery_avg_cost_sek_kwh,
                            pv_production_ratio=pv_production_ratio,
                        )
                        self._last_plan_ps_sig = ps_sig
                        _LOGGER.info("DayPlan byggd: %s | %s", self._day_plan.summary(), self._day_plan.notes)
                    except Exception as _plan_err:
                        _LOGGER.warning("DayPlan: kunde inte byggas: %s", _plan_err)

            if self._day_plan:
                _cs = self._day_plan.slot_at(now)
                state.plan_action = _cs.action if _cs else None
                state.plan_export_floor_kwh = self._day_plan.export_floor_kwh

            decision = self._controller.compute(state)
            self._last_decision = decision

            # Plan-executor: enda skrivställe för batteriets börvärden när en
            # plan finns (P2-5). Delad metod med backtest-simulatorn – se
            # EnergyController.apply_plan_executor() för hela resonemanget.
            decision = self._controller.apply_plan_executor(
                self._day_plan, self.operating_mode, state, decision, now, solar_surplus_w,
            )
            self._last_decision = decision

            # ── Jämför plan mot faktiskt (slutgiltigt, efter executorn) beslut ──
            if self._day_plan:
                plan_slot = self._day_plan.slot_at(now)
                if plan_slot:
                    # ctrl_export = aktivt nät-export (urladdning markant över huslasten).
                    # Ren husbehovstäckning (urladdning ≈ husunderskott) räknas som idle
                    # för planjämförelsen – annars skapas falskt AVVIKELSE på natten.
                    _house_load_w = state.house_load_w or 500
                    ctrl_export = (
                        decision.battery_discharge_power_w > 100
                        and decision.battery_discharge_power_w > _house_load_w + 200
                    )
                    ctrl_charge = decision.battery_charge_power_w > 100
                    ctrl_idle   = not ctrl_export and not ctrl_charge

                    plan_export = plan_slot.action == "export"
                    plan_charge = plan_slot.action in ("solar_charge", "grid_charge")
                    plan_idle   = plan_slot.action in ("idle", "cover_load")

                    match = (
                        (plan_export and ctrl_export)
                        or (plan_charge and ctrl_charge)
                        or (plan_idle and ctrl_idle)
                    )
                    if not match:
                        _LOGGER.warning(
                            "DayPlan AVVIKELSE kl %s: plan=%s %.0fW (%s) | faktiskt=%s chg=%.0fW dis=%.0fW | %s",
                            now.strftime("%H:%M"),
                            plan_slot.action, abs(plan_slot.target_power_w), plan_slot.reason,
                            "export" if ctrl_export else ("charge" if ctrl_charge else "idle"),
                            decision.battery_charge_power_w, decision.battery_discharge_power_w,
                            decision.reason[:120],
                        )
                    else:
                        _LOGGER.debug(
                            "DayPlan ✓ kl %s: plan=%s ≈ faktisk=%s",
                            now.strftime("%H:%M"), plan_slot.action,
                            "export" if ctrl_export else ("charge" if ctrl_charge else "idle"),
                        )

            if legionella_active:
                decision.reason = legionella_reason + " | " + decision.reason

            # Applicera minimitid-spärr på extra varmvatten innan exekvering
            self._apply_extra_hot_water_min_runtime(decision, now)

            # Skicka HA-notifieringar för laddare utan bilval
            if decision.chargers_needing_selection:
                await self._notify_car_selection_needed(decision.chargers_needing_selection)

            await self._check_battery_operating_mode()
            # Manual: EnergyController.compute() returnerar en tom ControlDecision
            # (alla fält på sitt default-värde: 0 W, enable=False) för att signalera
            # "inga beslut fattas" - men _execute_decision() skriver blint det den
            # får. Utan denna spärr skrevs den tomma decisionen till Sonnen/laddare/
            # varmvatten varje cykel, vilket i praktiken nollställde/stängde av allt
            # brukaren just då försökte styra manuellt - motsatsen till manual-lägets
            # syfte (CLAUDE.md: "används när du vill styra ... manuellt").
            if self.operating_mode != MODE_MANUAL:
                await self._execute_decision(state, decision, now)

            return {
                "state": state,
                "decision": decision,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "spot_price": spot_price,
                "house_load_w": house_load_w,
                "solar_power_w": solar_w,
                "solar_surplus_w": solar_surplus_w,
                "ev_total_power_w": sum(ch.power_w for ch in state.chargers),
                "battery_soc_pct": state.battery_soc_pct,
                "battery_min_soc": float(c.get(CONF_BATTERY_MIN_SOC, 20)),
                "battery_max_power_kw": state.battery_max_power_kw,
                "sell_solar_min_price": self._controller.sell_solar_min_price,
                "evening_target_soc_pct": decision.evening_target_soc if decision.evening_target_soc > 0 else (
                    self._day_plan.evening_target_soc_pct if self._day_plan else 30.0
                ),
                "legionella_active": legionella_active,
                "legionella_last_run": self._legionella.last_run,
                "legionella_days_since": self._legionella.days_since_last_run,
                "legionella_next_due": self._legionella.next_due(),
                "chargers_needing_selection": decision.chargers_needing_selection,
                "price_schedule": price_schedule,
                "yesterday_consumption_kwh": yesterday_kwh,
                "negative_slots_ahead": price_schedule.negative_slots_ahead if price_schedule else 0,
                "best_discharge_price": price_schedule.best_discharge_slot.buy_sek if price_schedule and price_schedule.best_discharge_slot else None,
                "best_charge_price": price_schedule.best_charge_slot.buy_sek if price_schedule and price_schedule.best_charge_slot else None,
                "solar_next_2h_kwh": price_schedule.solar_next_2h_kwh if price_schedule else 0.0,
                "solar_next_4h_kwh": price_schedule.solar_next_4h_kwh if price_schedule else 0.0,
                "solar_next_8h_kwh": price_schedule.solar_next_8h_kwh if price_schedule else 0.0,
                "peak_solar_kw_next_8h": price_schedule.peak_solar_kw_next_8h if price_schedule else 0.0,
                "hours_to_solar_peak": price_schedule.hours_to_solar_peak if price_schedule else 0.0,
                "should_wait_for_solar": price_schedule.should_wait_for_solar if price_schedule else False,
                "day_plan": self._day_plan,
                "pv_production_ratio": pv_production_ratio,
            }

        except Exception as err:
            _LOGGER.exception("Fel vid uppdatering av Smart Energy Manager")
            raise UpdateFailed(f"Error updating Smart Energy Manager: {err}") from err

    async def _check_battery_operating_mode(self) -> None:
        """Varna om batteriets egna driftläge inte står på 'manual' – utan detta
        är alla börvärden SEM skriver verkningslösa, tyst."""
        entity_id = self._config.get(CONF_BATTERY_OPERATING_MODE_ENTITY)
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        is_manual = state is not None and state.state.lower() == "manual"

        if not is_manual and not self._battery_mode_warning_active:
            self._battery_mode_warning_active = True
            _LOGGER.warning(
                "Batteriets driftläge (%s) står inte på 'manual' – SEM:s börvärden har ingen effekt",
                entity_id,
            )
            await self.hass.services.async_call(
                "persistent_notification", "create",
                {
                    "title": "⚠️ Smart Energy Manager – batteriet lyssnar inte",
                    "message": (
                        f"`{entity_id}` står inte på **manual**. SEM:s laddnings-/urladdningsbörvärden "
                        f"skrivs men har ingen effekt förrän driftläget ändras."
                    ),
                    "notification_id": "sem_battery_mode_warning",
                },
                blocking=False,
            )
        elif is_manual and self._battery_mode_warning_active:
            self._battery_mode_warning_active = False
            await self.hass.services.async_call(
                "persistent_notification", "dismiss",
                {"notification_id": "sem_battery_mode_warning"},
                blocking=False,
            )

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

    def _should_write_number(self, entity_id: str, target_value: float, now: datetime) -> bool:
        """P6-1: skriv bara om värdet ändrats mer än dödbandet, eller om
        heartbeat-intervallet passerat sedan senaste skrivningen (självläkning
        om en tidigare skrivning tappades bort). Jämför mot ENTITETENS LIVE-
        tillstånd, inte en egen cache – annars missas externa ändringar."""
        last_write = self._last_write_times.get(entity_id)
        if last_write is None or (now - last_write) >= _HEARTBEAT_INTERVAL:
            return True
        state = self.hass.states.get(entity_id)
        if state is None:
            return True
        try:
            current = float(state.state)
        except (ValueError, TypeError):
            return True
        return abs(current - target_value) > _POWER_DEADBAND_W

    def _should_write_switch(self, entity_id: str, target_on: bool, now: datetime) -> bool:
        """Som _should_write_number, men för switchar (exakt tillståndsjämförelse)."""
        last_write = self._last_write_times.get(entity_id)
        if last_write is None or (now - last_write) >= _HEARTBEAT_INTERVAL:
            return True
        state = self.hass.states.get(entity_id)
        if state is None:
            return True
        return (state.state == "on") != target_on

    async def _write_battery_setpoints(self, charge_w: float, discharge_w: float, now: Optional[datetime] = None) -> None:
        """Skriv batteriets börvärden – nolla alltid motsatt riktning FÖRST (blockerande)
        innan den aktiva riktningen sätts, så att Sonnen aldrig ser båda skilda från noll
        samtidigt vid ett riktningsbyte. P6-1-dödband hoppas bara över om `now` ges
        (async_zero_battery vid unload måste alltid skriva på riktigt, ingen deadband).

        Sonnens API-dokumentation antyder ETT delat internt börvärde (riktning+
        magnitud) bakom "Forcera laddning"/"Forcera urladdning", inte två oberoende
        register - senaste skrivningen vinner oavsett håll. Live-fall 2026-09-06:
        P6-1s hjärtslag (skriv om oförändrat värde minst var 5:e minut) skrev
        periodiskt om den INAKTIVA riktningens 0 mitt under en aktiv laddning/
        urladdning, vilket kortvarigt nollställde det delade börvärdet tills
        nästa cykel rättade till det - synligt som en enstaka nollpunkt i
        battery_inout var ~5-10:e minut. Hjärtslaget skrivs därför bara på den
        inaktiva riktningen när BÅDA ska vara 0 (verklig vila, inget aktivt håll
        håller börvärdet färskt) - annars bara vid en faktisk rättning."""
        charge_entity    = self._config.get(CONF_BATTERY_INVERTER_CHARGE)
        discharge_entity = self._config.get(CONF_BATTERY_INVERTER_DISCHARGE)

        async def _write(entity_id: Optional[str], value: float, blocking: bool, *, skip_heartbeat: bool = False) -> None:
            if not entity_id:
                return
            if now is not None:
                if skip_heartbeat:
                    state = self.hass.states.get(entity_id)
                    if state is not None:
                        try:
                            if abs(float(state.state) - value) <= _POWER_DEADBAND_W:
                                return
                        except (ValueError, TypeError):
                            pass
                elif not self._should_write_number(entity_id, value, now):
                    return
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": entity_id, "value": round(value)},
                blocking=blocking,
            )
            if now is not None:
                self._last_write_times[entity_id] = now

        if charge_w > 0:
            await _write(charge_entity, charge_w, blocking=False)
            if discharge_w <= 0:
                await _write(discharge_entity, 0, blocking=True, skip_heartbeat=True)
        elif discharge_w > 0:
            await _write(discharge_entity, discharge_w, blocking=False)
            if charge_w <= 0:
                await _write(charge_entity, 0, blocking=True, skip_heartbeat=True)
        else:
            await _write(charge_entity, 0, blocking=True)
            await _write(discharge_entity, 0, blocking=True)

    async def async_zero_battery(self) -> None:
        """Nolla båda batteribörvärdena. Anropas vid unload så att integrationen
        aldrig lämnar batteriet fruset i sitt senaste laddnings-/urladdningsläge."""
        await self._write_battery_setpoints(0.0, 0.0)

    async def _execute_decision(self, state: EnergyState, decision: ControlDecision, now: datetime) -> None:
        await self._write_battery_setpoints(
            decision.battery_charge_power_w, decision.battery_discharge_power_w, now
        )

        for ch_state, ch_dec in zip(state.chargers, decision.charger_decisions):
            cfg = ch_state.config
            if ch_dec.enable and ch_dec.current_a > 0 and cfg.charger_current:
                if self._should_write_number(cfg.charger_current, ch_dec.current_a, now):
                    await self.hass.services.async_call(
                        "number", "set_value",
                        {"entity_id": cfg.charger_current, "value": round(ch_dec.current_a)},
                        blocking=False,
                    )
                    self._last_write_times[cfg.charger_current] = now
            if cfg.charger_switch and self._should_write_switch(cfg.charger_switch, ch_dec.enable, now):
                service = "turn_on" if ch_dec.enable else "turn_off"
                await self.hass.services.async_call(
                    "switch", service,
                    {"entity_id": cfg.charger_switch},
                    blocking=False,
                )
                self._last_write_times[cfg.charger_switch] = now

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
        if hot_water_entity and self._should_write_switch(hot_water_entity, decision.extra_hot_water, now):
            service = "turn_on" if decision.extra_hot_water else "turn_off"
            await self.hass.services.async_call(
                "switch", service,
                {"entity_id": hot_water_entity},
                blocking=False,
            )
            self._last_write_times[hot_water_entity] = now

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

    def _apply_extra_hot_water_min_runtime(self, decision, now: datetime) -> None:
        """
        Förhindra att extra varmvatten flimrar på/av.

        Om styrlogiken nyss slog PÅ extra varmvatten och minimitiden inte
        har passerat, tvingar vi decision.extra_hot_water = True även om
        styrlogiken nu vill stänga av det. Minimitiden börjar räknas från
        den faktiska start-tidpunkten, inte varje cykel.
        """
        min_runtime_min = float(self._config.get(
            CONF_EXTRA_HOT_WATER_MIN_RUNTIME_MINUTES,
            DEFAULT_EXTRA_HOT_WATER_MIN_RUNTIME_MINUTES,
        ))

        wants_on = decision.extra_hot_water

        if wants_on and not self._extra_hot_water_actual_state:
            # Övergång AV → PÅ: starta timern
            self._extra_hot_water_started_at = now
            self._extra_hot_water_actual_state = True
            return

        if not wants_on and self._extra_hot_water_actual_state:
            # Styrlogiken vill stänga av – kolla om minimitiden har passerat
            if self._extra_hot_water_started_at is not None:
                elapsed_min = (now - self._extra_hot_water_started_at).total_seconds() / 60
                if elapsed_min < min_runtime_min:
                    # Tvinga kvar PÅ tills minimitiden är uppnådd
                    decision.extra_hot_water = True
                    decision.reason += (
                        f" | Extra varmvatten låst PÅ ({elapsed_min:.1f}/{min_runtime_min:.0f} min)"
                    )
                    return
            # Minimitiden har passerat – tillåt avstängning
            self._extra_hot_water_actual_state = False
            self._extra_hot_water_started_at = None
            return

        if wants_on and self._extra_hot_water_actual_state:
            # Fortsätter vara på – inget att göra
            return

        # not wants_on and not self._extra_hot_water_actual_state – redan av, inget att göra

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

    @property
    def day_plan(self) -> Optional[DayPlan]:
        return self._day_plan
