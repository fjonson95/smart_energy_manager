"""Sensors for Smart Energy Manager."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfPower, UnitOfElectricCurrent
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NO_CAR_SELECTED
from .coordinator import SmartEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        SmartEnergyBuyPriceSensor(coordinator, entry),
        SmartEnergySellPriceSensor(coordinator, entry),
        SmartEnergySpotPriceSensor(coordinator, entry),
        SmartEnergyBatteryChargePowerSensor(coordinator, entry),
        SmartEnergyBatteryDischargePowerSensor(coordinator, entry),
        SmartEnergyPhaseL1Sensor(coordinator, entry),
        SmartEnergyPhaseL2Sensor(coordinator, entry),
        SmartEnergyPhaseL3Sensor(coordinator, entry),
        SmartEnergyDecisionReasonSensor(coordinator, entry),
        SmartEnergyOperatingModeSensor(coordinator, entry),
        SmartEnergySolarSurplusSensor(coordinator, entry),
        SmartEnergyHouseLoadSensor(coordinator, entry),
        SmartEnergyLegionellaActiveSensor(coordinator, entry),
        SmartEnergyLegionellaDaysSinceSensor(coordinator, entry),
        SmartEnergyLegionellaNextDueSensor(coordinator, entry),
        SmartEnergyLegionellaTempConfirmedSensor(coordinator, entry),
    ]

    # Dynamiska sensorer per laddare
    for ch_data in coordinator._get_charger_configs():
        charger_name = ch_data.get("name", "Laddare")
        entities.append(ChargerCurrentSensor(coordinator, entry, charger_name))
        entities.append(ChargerEnabledSensor(coordinator, entry, charger_name))
        entities.append(ChargerActiveCarSensor(coordinator, entry, charger_name))
        entities.append(ChargerConnectedSensor(coordinator, entry, charger_name))

    # Temperatursensor om konfigurerad
    from .const import CONF_HOT_WATER_TEMP_ENTITY
    if coordinator._config.get(CONF_HOT_WATER_TEMP_ENTITY):
        entities.append(SmartEnergyHotWaterTempSensor(coordinator, entry))

    # Prisschema-sensorer
    entities += [
        SmartEnergyNegativeSlotsAheadSensor(coordinator, entry),
        SmartEnergyBestDischargePriceSensor(coordinator, entry),
        SmartEnergyBestChargePriceSensor(coordinator, entry),
        NordpoolPriceScheduleSensor(coordinator, entry),
    ]

    # Batterikostandssensorer:
    #   nät→batteri * köppris, sol→batteri * säljpris, urladdning * snittpris
    _bat_cost = BatteryAccumulatedCostSensor(coordinator, entry)
    entities += [
        _bat_cost,
        BatteryAveragePriceSensor(coordinator, entry, _bat_cost),
        BatteryEnergyKwhSensor(coordinator, entry),
    ]

    # Solprognos-sensorer (visas alltid, värde 0 om Solcast ej konfigurerat)
    entities += [
        SmartEnergySolarNext2hSensor(coordinator, entry),
        SmartEnergySolarNext4hSensor(coordinator, entry),
        SmartEnergySolarNext8hSensor(coordinator, entry),
        SmartEnergyPeakSolarKwSensor(coordinator, entry),
        SmartEnergyHoursToSolarPeakSensor(coordinator, entry),
        SmartEnergyWaitForSolarSensor(coordinator, entry),
        PvProductionRatioSensor(coordinator, entry),
    ]

    # Gårdagsförbrukning om konfigurerad
    from .const import CONF_YESTERDAY_CONSUMPTION_ENTITY, CONF_OUTDOOR_TEMP_ENTITY
    if coordinator._config.get(CONF_YESTERDAY_CONSUMPTION_ENTITY):
        entities.append(SmartEnergyYesterdayConsumptionSensor(coordinator, entry))

    # Förbrukningsprognos om utomhustemperatur är konfigurerad
    if coordinator._config.get(CONF_OUTDOOR_TEMP_ENTITY):
        entities.append(PredictedHouseLoadSensor(coordinator, entry))

    # Ekvivalenta fullcykler om batteriets AC-urladdningsräknare är konfigurerad
    from .const import CONF_BATTERY_AC_DISCHARGE_ENERGY_ENTITY
    if coordinator._config.get(CONF_BATTERY_AC_DISCHARGE_ENERGY_ENTITY):
        entities.append(BatteryEquivalentCyclesSensor(coordinator, entry))

    # EnergyPlanner-sensorer (plan vs faktisk)
    entities += [
        DayPlanActionSensor(coordinator, entry),
        DayPlanChargePowerSensor(coordinator, entry),
        DayPlanDischargePowerSensor(coordinator, entry),
        DayPlanReasonSensor(coordinator, entry),
        PlanExecutorChargeSensor(coordinator, entry),
        PlanExecutorDischargeSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class _BaseEnergySensor(CoordinatorEntity, SensorEntity):
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


class SmartEnergyBuyPriceSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_buy_price"
    _attr_translation_key = "buy_price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:currency-usd"

    @property
    def native_value(self):
        return round(self.coordinator.data.get("buy_price", 0.0), 4) if self.coordinator.data else None


class SmartEnergySellPriceSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_sell_price"
    _attr_translation_key = "sell_price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:currency-usd"

    @property
    def native_value(self):
        return round(self.coordinator.data.get("sell_price", 0.0), 4) if self.coordinator.data else None


class SmartEnergySpotPriceSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_spot_price"
    _attr_translation_key = "spot_price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:flash"

    @property
    def native_value(self):
        return round(self.coordinator.data.get("spot_price", 0.0), 4) if self.coordinator.data else None


class SmartEnergyBatteryChargePowerSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_battery_charge_power"
    _attr_translation_key = "battery_charge_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-up"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.battery_charge_power_w) if d else 0


class SmartEnergyBatteryDischargePowerSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_battery_discharge_power"
    _attr_translation_key = "battery_discharge_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-down"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.battery_discharge_power_w) if d else 0


# ── Laddarsensorer ────────────────────────────────────────────────────────────

class _BaseChargerSensor(_BaseEnergySensor):
    def __init__(self, coordinator, entry, charger_name: str):
        super().__init__(coordinator, entry)
        self._charger_name = charger_name
        self._safe_name = charger_name.lower().replace(" ", "_")

    def _find_charger_index(self):
        state = self.coordinator.current_state
        if not state:
            return -1
        for i, ch in enumerate(state.chargers):
            if ch.config.name == self._charger_name:
                return i
        return -1


class ChargerCurrentSensor(_BaseChargerSensor):
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry, charger_name: str):
        super().__init__(coordinator, entry, charger_name)
        self._attr_unique_id = f"sem_charger_{self._safe_name}_current"
        self._attr_name = f"{charger_name} Charge Current Setpoint"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        if not d:
            return 0
        idx = self._find_charger_index()
        if idx < 0 or idx >= len(d.charger_decisions):
            return 0
        dec = d.charger_decisions[idx]
        return round(dec.current_a) if dec.enable else 0


class ChargerEnabledSensor(_BaseChargerSensor):
    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator, entry, charger_name: str):
        super().__init__(coordinator, entry, charger_name)
        self._attr_unique_id = f"sem_charger_{self._safe_name}_enabled"
        self._attr_name = f"{charger_name} Charging Enabled"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        if not d:
            return "off"
        idx = self._find_charger_index()
        if idx < 0 or idx >= len(d.charger_decisions):
            return "off"
        return "on" if d.charger_decisions[idx].enable else "off"


class ChargerActiveCarSensor(_BaseChargerSensor):
    """Visar vilken bil som är vald på laddaren."""
    _attr_icon = "mdi:car-info"

    def __init__(self, coordinator, entry, charger_name: str):
        super().__init__(coordinator, entry, charger_name)
        self._attr_unique_id = f"sem_charger_{self._safe_name}_active_car"
        self._attr_name = f"{charger_name} Active Car"

    @property
    def native_value(self):
        return self.coordinator.get_active_car(self._charger_name)


class ChargerConnectedSensor(_BaseChargerSensor):
    """Visar om en bil är fysiskt ansluten till laddaren."""
    _attr_icon = "mdi:cable-data"

    def __init__(self, coordinator, entry, charger_name: str):
        super().__init__(coordinator, entry, charger_name)
        self._attr_unique_id = f"sem_charger_{self._safe_name}_connected"
        self._attr_name = f"{charger_name} Connected"

    @property
    def native_value(self):
        state = self.coordinator.current_state
        if not state:
            return "off"
        idx = self._find_charger_index()
        if idx < 0:
            return "off"
        return "on" if state.chargers[idx].connected else "off"


# ── Övriga sensorer ───────────────────────────────────────────────────────────

class SmartEnergyPhaseL1Sensor(_BaseEnergySensor):
    _attr_unique_id = "sem_phase_l1_load"
    _attr_translation_key = "phase_l1_load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.phase_loads.L1) if d else 0


class SmartEnergyPhaseL2Sensor(_BaseEnergySensor):
    _attr_unique_id = "sem_phase_l2_load"
    _attr_translation_key = "phase_l2_load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.phase_loads.L2) if d else 0


class SmartEnergyPhaseL3Sensor(_BaseEnergySensor):
    _attr_unique_id = "sem_phase_l3_load"
    _attr_translation_key = "phase_l3_load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.phase_loads.L3) if d else 0


class SmartEnergyDecisionReasonSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_decision_reason"
    _attr_translation_key = "decision_reason"
    _attr_icon = "mdi:information"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return d.reason[:255] if d else "No decision yet"


class SmartEnergyOperatingModeSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_operating_mode"
    _attr_translation_key = "operating_mode_sensor"
    _attr_icon = "mdi:cog"

    @property
    def native_value(self):
        return self.coordinator.operating_mode


class SmartEnergyLegionellaActiveSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_legionella_active"
    _attr_translation_key = "legionella_active"
    _attr_icon = "mdi:bacteria"

    @property
    def native_value(self):
        d = self.coordinator.data
        return "on" if (d and d.get("legionella_active")) else "off"


class SmartEnergyLegionellaDaysSinceSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_legionella_days_since"
    _attr_translation_key = "legionella_days_since"
    _attr_native_unit_of_measurement = "d"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self):
        d = self.coordinator.data
        if not d:
            return None
        days = d.get("legionella_days_since")
        return round(days, 1) if days is not None else None


class SmartEnergyLegionellaNextDueSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_legionella_next_due"
    _attr_translation_key = "legionella_next_due"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-alert"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("legionella_next_due") if d else None


class SmartEnergyHouseLoadSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_house_load"
    _attr_translation_key = "house_load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-lightning-bolt"

    @property
    def native_value(self):
        s = self.coordinator.current_state
        return round(s.house_load_w) if s else 0


class SmartEnergySolarSurplusSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_solar_surplus"
    _attr_translation_key = "solar_surplus"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d.get("solar_surplus_w", 0.0)) if d else 0


class SmartEnergyHotWaterTempSensor(_BaseEnergySensor):
    """Ackumulatortankens temperatur."""
    _attr_unique_id = "sem_hot_water_temp"
    _attr_translation_key = "hot_water_temp"
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer-water"

    @property
    def native_value(self):
        s = self.coordinator.current_state
        if not s or s.hot_water_temp_c is None:
            return None
        return round(s.hot_water_temp_c, 1)


class SmartEnergyLegionellaTempConfirmedSensor(_BaseEnergySensor):
    """Visar om legionella-körningen bekräftats via temperatur."""
    _attr_unique_id = "sem_legionella_temp_confirmed"
    _attr_translation_key = "legionella_temp_confirmed"
    _attr_icon = "mdi:thermometer-check"

    @property
    def native_value(self) -> str:
        return "on" if self.coordinator.legionella._temp_confirmed else "off"


# ── Prisschema-sensorer ───────────────────────────────────────────────────────

class SmartEnergyNegativeSlotsAheadSensor(_BaseEnergySensor):
    """Antal kvartstimmar med negativt säljpris kommande 8h."""
    _attr_unique_id = "sem_negative_slots_ahead"
    _attr_translation_key = "negative_slots_ahead"
    _attr_native_unit_of_measurement = "slots"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-alert"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("negative_slots_ahead", 0) if d else 0

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        ps = d.get("price_schedule") if d else None
        if not ps:
            return {}
        return {
            "low_price_slots_ahead": ps.low_price_slots_ahead,
            "should_absorb_proactively": ps.should_absorb_proactively,
            "recommended_headroom_pct": round(ps.recommended_headroom * 100, 1),
            "avg_price_next_4h_sek": round(ps.avg_price_next_4h, 4),
            "max_price_next_12h_sek": round(ps.max_price_next_12h, 4),
            "min_price_next_12h_sek": round(ps.min_price_next_12h, 4),
        }


class SmartEnergyBestDischargePriceSensor(_BaseEnergySensor):
    """Bästa köppris för batteridischarge kommande 12h."""
    _attr_unique_id = "sem_best_discharge_price"
    _attr_translation_key = "best_discharge_price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-down"

    @property
    def native_value(self):
        ps = self._get_schedule()
        if ps is None or ps.best_discharge_slot is None:
            return None
        return round(ps.best_discharge_slot.buy_sek, 4)

    @property
    def extra_state_attributes(self):
        ps = self._get_schedule()
        if not ps or not ps.best_discharge_slot:
            return {}
        return {
            "best_discharge_time": ps.best_discharge_slot.start.isoformat(),
            "best_discharge_spot_sek": round(ps.best_discharge_slot.spot_sek, 4),
        }

    def _get_schedule(self):
        d = self.coordinator.data
        return d.get("price_schedule") if d else None


class SmartEnergyBestChargePriceSensor(_BaseEnergySensor):
    """Lägsta köppris för batteriladdning kommande 12h."""
    _attr_unique_id = "sem_best_charge_price"
    _attr_translation_key = "best_charge_price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-up"

    @property
    def native_value(self):
        ps = self._get_schedule()
        if ps is None or ps.best_charge_slot is None:
            return None
        return round(ps.best_charge_slot.buy_sek, 4)

    @property
    def extra_state_attributes(self):
        ps = self._get_schedule()
        if not ps or not ps.best_charge_slot:
            return {}
        return {
            "best_charge_time": ps.best_charge_slot.start.isoformat(),
            "best_charge_spot_sek": round(ps.best_charge_slot.spot_sek, 4),
        }

    def _get_schedule(self):
        d = self.coordinator.data
        return d.get("price_schedule") if d else None


class SmartEnergyYesterdayConsumptionSensor(_BaseEnergySensor):
    """Gårdagens förbrukning exkl. EV-laddning."""
    _attr_unique_id = "sem_yesterday_consumption"
    _attr_translation_key = "yesterday_consumption"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:home-lightning-bolt-outline"

    @property
    def native_value(self):
        d = self.coordinator.data
        val = d.get("yesterday_consumption_kwh") if d else None
        return round(val, 2) if val is not None else None


# ── Solprognos-sensorer ───────────────────────────────────────────────────────

class SmartEnergySolarNext2hSensor(_BaseEnergySensor):
    """Förväntad solenergi kommande 2 timmar (kWh, median)."""
    _attr_unique_id = "sem_solar_next_2h_kwh"
    _attr_translation_key = "solar_next_2h"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = None
    _attr_icon = "mdi:weather-sunny"

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d.get("solar_next_2h_kwh", 0.0), 2) if d else 0.0


class SmartEnergySolarNext4hSensor(_BaseEnergySensor):
    """Förväntad solenergi kommande 4 timmar (kWh, median)."""
    _attr_unique_id = "sem_solar_next_4h_kwh"
    _attr_translation_key = "solar_next_4h"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = None
    _attr_icon = "mdi:weather-sunny"

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d.get("solar_next_4h_kwh", 0.0), 2) if d else 0.0


class PvProductionRatioSensor(_BaseEnergySensor):
    """P3-2: rullande 3-dygns kvot faktisk/prognos solproduktion.

    ~1.0 = normal produktion. Lågt värde (t.ex. <0.6) indikerar att
    Solcast-prognosen slår fel – snötäckta eller nedsmutsade paneler.
    Golvet i DayPlan skalas upp automatiskt när kvoten sjunker.
    """
    _attr_unique_id = "sem_pv_production_ratio"
    _attr_translation_key = "pv_production_ratio"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-snowy"

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d.get("pv_production_ratio", 1.0), 2) if d else 1.0


class SmartEnergySolarNext8hSensor(_BaseEnergySensor):
    """Förväntad solenergi kommande 8 timmar (kWh, median)."""
    _attr_unique_id = "sem_solar_next_8h_kwh"
    _attr_translation_key = "solar_next_8h"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = None
    _attr_icon = "mdi:weather-sunny-alert"

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d.get("solar_next_8h_kwh", 0.0), 2) if d else 0.0


class SmartEnergyPeakSolarKwSensor(_BaseEnergySensor):
    """Maxeffekt från sol kommande 8h (kW)."""
    _attr_unique_id = "sem_peak_solar_kw_next_8h"
    _attr_translation_key = "peak_solar_power"
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power-variant"

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d.get("peak_solar_kw_next_8h", 0.0), 2) if d else 0.0

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        ps = d.get("price_schedule") if d else None
        if not ps or not ps.peak_solar_time:
            return {}
        return {"peak_solar_time": ps.peak_solar_time.isoformat()}


class SmartEnergyHoursToSolarPeakSensor(_BaseEnergySensor):
    """Timmar tills soltoppen kommande 8h."""
    _attr_unique_id = "sem_hours_to_solar_peak"
    _attr_translation_key = "hours_to_solar_peak"
    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-time-eight-outline"

    @property
    def native_value(self):
        d = self.coordinator.data
        return round(d.get("hours_to_solar_peak", 0.0), 1) if d else 0.0


class SmartEnergyWaitForSolarSensor(_BaseEnergySensor):
    """Indikerar om det lönar sig att vänta på sol innan batteriladdning från nät."""
    _attr_unique_id = "sem_wait_for_solar"
    _attr_translation_key = "wait_for_solar"
    _attr_icon = "mdi:sun-clock"

    @property
    def native_value(self) -> str:
        d = self.coordinator.data
        return "on" if (d and d.get("should_wait_for_solar")) else "off"

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        if not d:
            return {}
        return {
            "solar_next_2h_kwh": round(d.get("solar_next_2h_kwh", 0.0), 2),
            "hours_to_solar_peak": round(d.get("hours_to_solar_peak", 0.0), 1),
            "peak_solar_kw": round(d.get("peak_solar_kw_next_8h", 0.0), 2),
        }


# ── Batterikostnadssensorer ───────────────────────────────────────────────────

class BatteryAccumulatedCostSensor(_BaseEnergySensor, RestoreEntity):
    """Ackumulerad kostnad för energin som finns i batteriet.

    Modell:
      - Nät → batteri:  grid_kwh  * köppris
      - Sol → batteri:  solar_kwh * säljpris  (alternativkostnad – du offrar sälj-intäkten)
      - Urladdning:     cost *= new_energy / old_energy  (= discharge_kwh * snittpris)

    battery_power_w > 0 = laddar, < 0 = laddar ur.
    """

    _attr_unique_id  = "sem_battery_accumulated_cost"
    _attr_translation_key = "battery_accumulated_cost"
    _attr_native_unit_of_measurement = "SEK"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class  = SensorStateClass.TOTAL
    _attr_icon         = "mdi:battery-charging-medium"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._cost_sek: float = 0.0
        self._last_update: datetime | None = None
        self._last_soc: float | None = None
        self._solar_kwh_total: float = 0.0
        self._grid_kwh_total: float = 0.0
        self._last_sell_price: float = 0.0

    def reset_cost(self) -> None:
        self._cost_sek = 0.0
        self._solar_kwh_total = 0.0
        self._grid_kwh_total = 0.0
        self._last_soc = None
        self._last_update = None
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator._battery_cost_reset_cb = self.reset_cost
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable", None):
            try:
                self._cost_sek        = float(last.attributes.get("accumulated_cost_sek", 0.0))
                self._solar_kwh_total = float(last.attributes.get("solar_kwh_total", 0.0))
                self._grid_kwh_total  = float(last.attributes.get("grid_kwh_total", 0.0))
            except (ValueError, TypeError):
                self._cost_sek = 0.0

    def _handle_coordinator_update(self) -> None:
        now = dt_util.now()
        state = self.coordinator.current_state

        if state is not None and self._last_update is not None and self._last_soc is not None:
            dt_h = (now - self._last_update).total_seconds() / 3600.0

            battery_w  = state.battery_power_w
            solar_w    = state.solar_power_w
            house_w    = state.house_load_w
            soc        = state.battery_soc_pct
            capacity   = state.battery_capacity_kwh
            buy_price  = max(0.0, state.buy_price_sek_kwh)
            sell_price = state.sell_price_sek_kwh
            self._last_sell_price = sell_price

            if battery_w > 0:
                surplus_w    = max(0.0, solar_w - house_w)
                solar_to_bat = min(battery_w, surplus_w)
                grid_to_bat  = max(0.0, battery_w - solar_to_bat)

                grid_kwh  = grid_to_bat  / 1000.0 * dt_h
                solar_kwh = solar_to_bat / 1000.0 * dt_h

                self._grid_kwh_total  += grid_kwh
                self._solar_kwh_total += solar_kwh
                self._cost_sek += grid_kwh * buy_price + solar_kwh * sell_price

            elif battery_w < 0:
                old_energy = self._last_soc / 100.0 * capacity
                new_energy = soc           / 100.0 * capacity
                if old_energy > 0.01:
                    self._cost_sek *= max(0.0, new_energy / old_energy)
                self._cost_sek = max(0.0, self._cost_sek)

        if state is not None:
            self._last_soc = state.battery_soc_pct
        self._last_update = now

        energy = self._energy_in_battery_kwh()
        avg = self._cost_sek / energy if energy > 0.01 else 0.0
        self.coordinator._battery_avg_cost_sek_kwh = avg

        super()._handle_coordinator_update()

    def _energy_in_battery_kwh(self) -> float:
        s = self.coordinator.current_state
        if not s:
            return 0.0
        return s.battery_soc_pct / 100.0 * s.battery_capacity_kwh

    @property
    def native_value(self) -> float:
        return round(self._cost_sek, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        energy = self._energy_in_battery_kwh()
        avg    = self._cost_sek / energy if energy > 0.01 else 0.0
        return {
            "accumulated_cost_sek":  round(self._cost_sek, 4),
            "energy_in_battery_kwh": round(energy, 2),
            "average_price_sek_kwh": round(avg, 4),
            "solar_kwh_total":       round(self._solar_kwh_total, 4),
            "grid_kwh_total":        round(self._grid_kwh_total, 4),
            "last_sell_price":       round(self._last_sell_price, 4),
        }


class BatteryAveragePriceSensor(_BaseEnergySensor, RestoreEntity):
    """Snittpris per kWh för energin som finns i batteriet.

    Läser direkt från BatteryAccumulatedCostSensor för att alltid vara konsistent
    med accumulated_cost / energy_in_battery. Ingen egen ackumulator.
    """

    _attr_unique_id  = "sem_battery_average_price"
    _attr_translation_key = "battery_average_price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_state_class  = SensorStateClass.MEASUREMENT
    _attr_icon         = "mdi:battery-heart"

    def __init__(self, coordinator, entry, cost_sensor: "BatteryAccumulatedCostSensor"):
        super().__init__(coordinator, entry)
        self._cost_sensor = cost_sensor

    def _energy_in_battery_kwh(self) -> float:
        s = self.coordinator.current_state
        if not s:
            return 0.0
        return s.battery_soc_pct / 100.0 * s.battery_capacity_kwh

    @property
    def native_value(self) -> float:
        energy = self._energy_in_battery_kwh()
        cost   = self._cost_sensor._cost_sek
        return round(cost / energy, 4) if energy > 0.01 else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        energy = self._energy_in_battery_kwh()
        cost   = self._cost_sensor._cost_sek
        return {
            "accumulated_cost_sek":  round(cost, 4),
            "energy_in_battery_kwh": round(energy, 2),
        }


class PredictedHouseLoadSensor(_BaseEnergySensor):
    """Beräknad daglig husförbrukning (kWh) baserat på utomhustemperatur.

    Modell: base_dhw + k × max(0, T_balance − temp)
    Kalibrerad mot helårsdata (docs/forbrukningsanalys.md, avsnitt 7):
      base_dhw = 1,0 kWh, k = 2,39 kWh/gradddag, T_balance = 12 °C
    (se DEFAULT_BASE_DHW_KWH/DEFAULT_HEAT_FACTOR_KWH_DD/DEFAULT_HEAT_BALANCE_TEMP
    i const.py för de faktiska default-värdena koden läser)
    """

    _attr_unique_id   = "sem_predicted_house_load"
    _attr_translation_key = "predicted_house_load"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class  = None
    _attr_icon         = "mdi:home-lightning-bolt"

    @property
    def native_value(self) -> float | None:
        s = self.coordinator.current_state
        if not s or s.predicted_daily_kwh == 0.0:
            return None
        return round(s.predicted_daily_kwh, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.current_state
        if not s:
            return {}
        from .const import DEFAULT_HEAT_BALANCE_TEMP, DEFAULT_BASE_DHW_KWH, DEFAULT_DISINFECTING_EXTRA_KWH
        t_bal = float(self.coordinator._config.get("heat_balance_temp", DEFAULT_HEAT_BALANCE_TEMP))
        base  = float(self.coordinator._config.get("base_dhw_kwh", DEFAULT_BASE_DHW_KWH))
        extra = float(self.coordinator._config.get("disinfecting_extra_kwh", DEFAULT_DISINFECTING_EXTRA_KWH))
        temp  = s.avg_temp_yesterday_c if s.avg_temp_yesterday_c is not None else s.outdoor_temp_c
        hdd   = max(0.0, t_bal - temp) if temp is not None else None
        return {
            "temp_used_c":          round(temp, 1) if temp is not None else None,
            "temp_source":          "yesterday_avg" if s.avg_temp_yesterday_c is not None else "current",
            "outdoor_temp_c":       round(s.outdoor_temp_c, 1) if s.outdoor_temp_c is not None else None,
            "avg_temp_yesterday_c": round(s.avg_temp_yesterday_c, 1) if s.avg_temp_yesterday_c is not None else None,
            "heating_degree_day":   round(hdd, 1) if hdd is not None else None,
            "dhw_base_kwh":         round(base, 2),
            "heating_kwh":          round(max(0.0, s.predicted_daily_kwh - base - (extra if s.disinfecting_active else 0.0)), 2),
            "disinfecting_active":  s.disinfecting_active,
            "disinfecting_extra_kwh": round(extra, 2) if s.disinfecting_active else 0.0,
        }


class BatteryEnergyKwhSensor(_BaseEnergySensor):
    """Faktisk energi lagrad i batteriet (kWh), beräknat från SOC × kapacitet."""

    _attr_unique_id   = "sem_battery_energy_kwh"
    _attr_translation_key = "battery_energy"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class  = None
    _attr_icon         = "mdi:battery-charging-medium"

    @property
    def native_value(self) -> float | None:
        s = self.coordinator.current_state
        if not s:
            return None
        return round(s.battery_soc_pct / 100.0 * s.battery_capacity_kwh, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.current_state
        if not s:
            return {}
        return {
            "soc_pct":            round(s.battery_soc_pct, 1),
            "capacity_kwh":       s.battery_capacity_kwh,
            "remaining_capacity_kwh": round((1 - s.battery_soc_pct / 100.0) * s.battery_capacity_kwh, 2),
        }


class BatteryEquivalentCyclesSensor(_BaseEnergySensor):
    """Ackumulerade ekvivalenta fullcykler (v1.0 steg 1).

    fullcykler = ackumulerad AC-urladdning (battery_ac_discharge_energy_entity)
    / användbar batterikapacitet – samma AC-räknare eta_roundtrip kalibrerades
    mot 2026-09-08 (0,849, uppmätt på just den här anläggningen). Gör att
    antagandet om cykelkostnaden går att övervaka mot verklig cyklingstakt
    istället för att bara förutsättas – garantin (10 år eller 10 000 cykler)
    binder vid kalendern, inte cykelantalet, så länge takten ligger under
    ~2,74 cykler/dygn (10 000/3650); se docs/v1_implementation_plan.md steg 1.
    """

    _attr_unique_id   = "sem_battery_equivalent_cycles"
    _attr_translation_key = "battery_equivalent_cycles"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon        = "mdi:battery-sync"

    @property
    def native_value(self) -> float | None:
        s = self.coordinator.current_state
        if not s or s.battery_ac_discharge_energy_kwh is None or s.battery_capacity_kwh <= 0:
            return None
        return round(s.battery_ac_discharge_energy_kwh / s.battery_capacity_kwh, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.current_state
        if not s:
            return {}
        attrs: dict[str, Any] = {
            "discharge_energy_kwh": s.battery_ac_discharge_energy_kwh,
            "charge_energy_kwh":    s.battery_ac_charge_energy_kwh,
            "capacity_kwh":         s.battery_capacity_kwh,
        }
        if s.battery_ac_discharge_energy_kwh and s.battery_ac_charge_energy_kwh:
            attrs["measured_eta_roundtrip"] = round(
                s.battery_ac_discharge_energy_kwh / s.battery_ac_charge_energy_kwh, 4
            )
        return attrs


class NordpoolPriceScheduleSensor(_BaseEnergySensor):
    """Prisschema från Nordpool – alla dagens (och morgondagens) slots som attribut.

    Lämplig för visualisering med ApexCharts eller liknande Lovelace-kort.
    State = aktuellt spotpris (SEK/kWh).
    """

    _attr_unique_id = "sem_nordpool_price_schedule"
    _attr_translation_key = "nordpool_price_schedule"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = None
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        # P6-2: attributdicten byggdes tidigare om vid VARJE avläsning
        # (2,57s/uppdatering) trots att price_schedule bara byter identitet
        # när Nordpool/Solcast-data faktiskt ändras. Cacha på objektidentitet.
        self._cached_ps = None
        self._cached_attrs: dict[str, Any] = {
            "prices_today": [], "prices_tomorrow": [],
            "slot_count_today": 0, "slot_count_tomorrow": 0,
            "total_slot_count": 0,
        }

    @property
    def native_value(self) -> float | None:
        s = self.coordinator.current_state
        if not s:
            return None
        return round(s.spot_price_sek_kwh, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        ps = d.get("price_schedule") if d else None
        if ps is self._cached_ps:
            return self._cached_attrs

        self._cached_ps = ps
        if not ps or not ps.slots:
            self._cached_attrs = {
                "prices_today": [], "prices_tomorrow": [],
                "slot_count_today": 0, "slot_count_tomorrow": 0,
                "total_slot_count": 0,
            }
            return self._cached_attrs

        now = dt_util.now()
        midnight_tomorrow = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))

        prices_today = []
        prices_tomorrow = []
        for slot in ps.slots:
            start_local = slot.start.astimezone()
            entry = {
                "start": start_local.isoformat(),
                "end":   slot.end.astimezone().isoformat(),
                "spot":  round(slot.spot_sek, 4),
                "buy":   round(slot.buy_sek, 4),
                "sell":  round(slot.sell_sek, 4),
            }
            if start_local < midnight_tomorrow:
                prices_today.append(entry)
            else:
                prices_tomorrow.append(entry)

        self._cached_attrs = {
            "prices_today":        prices_today,
            "prices_tomorrow":     prices_tomorrow,
            "slot_count_today":    len(prices_today),
            "slot_count_tomorrow": len(prices_tomorrow),
            "total_slot_count":    len(ps.slots),
        }
        return self._cached_attrs


# ── EnergyPlanner-sensorer ───────────────────────────────────────────────────

class _DayPlanBase(_BaseEnergySensor):
    """Bas för plan-sensorer – hämtar aktuell planslott."""

    def _current_slot(self):
        plan = self.coordinator.day_plan
        if not plan:
            return None
        return plan.slot_at(dt_util.now())


class DayPlanActionSensor(_DayPlanBase):
    _attr_unique_id = "sem_day_plan_action"
    _attr_translation_key = "day_plan_action"
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> str:
        s = self._current_slot()
        return s.action if s else "unknown"


class DayPlanChargePowerSensor(_DayPlanBase):
    _attr_unique_id = "sem_day_plan_charge_power"
    _attr_translation_key = "day_plan_charge_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-up-outline"

    @property
    def native_value(self) -> float:
        s = self._current_slot()
        if not s:
            return 0.0
        return round(max(0.0, s.target_power_w), 0)


class DayPlanDischargePowerSensor(_DayPlanBase):
    _attr_unique_id = "sem_day_plan_discharge_power"
    _attr_translation_key = "day_plan_discharge_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-down-outline"

    @property
    def native_value(self) -> float:
        s = self._current_slot()
        if not s:
            return 0.0
        return round(max(0.0, -s.target_power_w), 0)


class DayPlanReasonSensor(_DayPlanBase):
    _attr_unique_id = "sem_day_plan_reason"
    _attr_translation_key = "day_plan_reason"
    _attr_icon = "mdi:information-outline"

    @property
    def native_value(self) -> str:
        s = self._current_slot()
        return s.reason[:255] if s else "Ingen plan"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.coordinator.day_plan
        s = self._current_slot()
        if not plan:
            return {}
        return {
            "battery_soc_est_pct": s.battery_soc_est_pct if s else None,
            "export_floor_kwh": round(plan.export_floor_kwh, 2),
            "evening_target_soc_pct": round(plan.evening_target_soc_pct, 1),
            "total_exportable_kwh": round(plan.total_exportable_kwh, 2),
            "hourly_load_kw": round(plan.hourly_load_kw, 2),
            "pv_production_ratio": round(plan.pv_production_ratio, 2),
            "solar_takeover": plan.solar_takeover_dt.isoformat() if plan.solar_takeover_dt else None,
            "plan_generated_at": plan.generated_at.isoformat(),
            # P6-2: begränsad till 24h – hela 28h-horisonten sprängde recorderns
            # 16 kB-gräns för attribut och slutade sparas alls.
            "slots": [
                {
                    "start": slot.start.isoformat(),
                    "end": slot.end.isoformat(),
                    "action": slot.action,
                    "target_power_w": round(slot.target_power_w, 0),
                    "battery_soc_est_pct": round(slot.battery_soc_est_pct, 1),
                }
                for slot in plan.slots
                if slot.start < dt_util.now() + timedelta(hours=24)
            ],
        }


class PlanExecutorChargeSensor(_DayPlanBase):
    """Laddningsbörvärdet som coordinatorns plan-executor faktiskt satte.

    Läser bara resultatet från coordinator.last_decision – executorn (i
    coordinator.py) är den enda platsen som räknar ut det faktiska värdet.
    """

    _attr_unique_id = "sem_plan_executor_charge"
    _attr_translation_key = "plan_executor_charge"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt-circle"

    @property
    def native_value(self) -> float:
        d = self.coordinator.last_decision
        return round(d.battery_charge_power_w, 0) if d else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._current_slot()
        return {"action": s.action if s else "unknown"}


class PlanExecutorDischargeSensor(_DayPlanBase):
    """Urladdningsbörvärdet som coordinatorns plan-executor faktiskt satte.

    Läser bara resultatet från coordinator.last_decision – executorn (i
    coordinator.py) är den enda platsen som räknar ut det faktiska värdet.
    """

    _attr_unique_id = "sem_plan_executor_discharge"
    _attr_translation_key = "plan_executor_discharge"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt-outline"

    @property
    def native_value(self) -> float:
        d = self.coordinator.last_decision
        return round(d.battery_discharge_power_w, 0) if d else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._current_slot()
        return {"action": s.action if s else "unknown"}
