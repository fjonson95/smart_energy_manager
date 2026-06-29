"""Sensors for Smart Energy Manager."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfPower, UnitOfElectricCurrent

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
    ]

    # Gårdagsförbrukning om konfigurerad
    from .const import CONF_YESTERDAY_CONSUMPTION_ENTITY
    if coordinator._config.get(CONF_YESTERDAY_CONSUMPTION_ENTITY):
        entities.append(SmartEnergyYesterdayConsumptionSensor(coordinator, entry))

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
    _attr_name = "Buy Price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:currency-usd"

    @property
    def native_value(self):
        return round(self.coordinator.data.get("buy_price", 0.0), 4) if self.coordinator.data else None


class SmartEnergySellPriceSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_sell_price"
    _attr_name = "Sell Price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:currency-usd"

    @property
    def native_value(self):
        return round(self.coordinator.data.get("sell_price", 0.0), 4) if self.coordinator.data else None


class SmartEnergySpotPriceSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_spot_price"
    _attr_name = "Spot Price"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    @property
    def native_value(self):
        return round(self.coordinator.data.get("spot_price", 0.0), 4) if self.coordinator.data else None


class SmartEnergyBatteryChargePowerSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_battery_charge_power"
    _attr_name = "Battery Charge Power Setpoint"
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
    _attr_name = "Battery Discharge Power Setpoint"
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
    _attr_name = "Phase L1 Estimated Load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.phase_loads.L1) if d else 0


class SmartEnergyPhaseL2Sensor(_BaseEnergySensor):
    _attr_unique_id = "sem_phase_l2_load"
    _attr_name = "Phase L2 Estimated Load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.phase_loads.L2) if d else 0


class SmartEnergyPhaseL3Sensor(_BaseEnergySensor):
    _attr_unique_id = "sem_phase_l3_load"
    _attr_name = "Phase L3 Estimated Load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return round(d.phase_loads.L3) if d else 0


class SmartEnergyDecisionReasonSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_decision_reason"
    _attr_name = "Last Decision Reason"
    _attr_icon = "mdi:information"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        return d.reason[:255] if d else "No decision yet"


class SmartEnergyOperatingModeSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_operating_mode"
    _attr_name = "Operating Mode"
    _attr_icon = "mdi:cog"

    @property
    def native_value(self):
        return self.coordinator.operating_mode


class SmartEnergyLegionellaActiveSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_legionella_active"
    _attr_name = "Legionella Disinfection Active"
    _attr_icon = "mdi:bacteria"

    @property
    def native_value(self):
        d = self.coordinator.data
        return "on" if (d and d.get("legionella_active")) else "off"


class SmartEnergyLegionellaDaysSinceSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_legionella_days_since"
    _attr_name = "Legionella Days Since Last Run"
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
    _attr_name = "Legionella Next Due"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-alert"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("legionella_next_due") if d else None


class SmartEnergyHouseLoadSensor(_BaseEnergySensor):
    _attr_unique_id = "sem_house_load"
    _attr_name = "House Load"
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
    _attr_name = "Solar Surplus"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"

    @property
    def native_value(self):
        s = self.coordinator.current_state
        if not s:
            return 0
        house_load = max(0.0, s.grid_power_l1 + s.grid_power_l2 + s.grid_power_l3 + s.solar_power_w)
        return round(max(0.0, s.solar_power_w - house_load))


class SmartEnergyHotWaterTempSensor(_BaseEnergySensor):
    """Ackumulatortankens temperatur."""
    _attr_unique_id = "sem_hot_water_temp"
    _attr_name = "Hot Water Temperature"
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
    _attr_name = "Legionella Temperature Confirmed"
    _attr_icon = "mdi:thermometer-check"

    @property
    def native_value(self) -> str:
        return "on" if self.coordinator.legionella._temp_confirmed else "off"


# ── Prisschema-sensorer ───────────────────────────────────────────────────────

class SmartEnergyNegativeSlotsAheadSensor(_BaseEnergySensor):
    """Antal kvartstimmar med negativt säljpris kommande 8h."""
    _attr_unique_id = "sem_negative_slots_ahead"
    _attr_name = "Negative Price Slots Ahead"
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
    _attr_name = "Best Discharge Price Next 12h"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
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
    _attr_name = "Best Charge Price Next 12h"
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
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
    _attr_name = "Yesterday Consumption (excl. EV)"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-lightning-bolt-outline"

    @property
    def native_value(self):
        d = self.coordinator.data
        val = d.get("yesterday_consumption_kwh") if d else None
        return round(val, 2) if val is not None else None
