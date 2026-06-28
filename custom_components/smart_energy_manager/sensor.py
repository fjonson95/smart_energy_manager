"""Sensors for Smart Energy Manager."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity, SensorDeviceClass, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfPower, UnitOfElectricCurrent

from .const import DOMAIN
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

    # Dynamic per-car sensors
    cars_cfg = entry.data.get("ev_cars", [])
    for i, car in enumerate(cars_cfg):
        name = car.get("name", f"Car {i+1}")
        entities.append(SmartEnergyEvCarCurrentSensor(coordinator, entry, i, name))
        entities.append(SmartEnergyEvCarEnabledSensor(coordinator, entry, i, name))

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


class SmartEnergyEvCarCurrentSensor(_BaseEnergySensor):
    """Current setpoint sensor for one specific EV car."""
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry, car_index: int, car_name: str):
        super().__init__(coordinator, entry)
        self._car_index = car_index
        self._attr_unique_id = f"sem_ev_car_{car_index}_current"
        self._attr_name = f"{car_name} Charge Current Setpoint"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        if not d or self._car_index >= len(d.ev_decisions):
            return 0
        ev_dec = d.ev_decisions[self._car_index]
        return round(ev_dec.current_a) if ev_dec.enable else 0


class SmartEnergyEvCarEnabledSensor(_BaseEnergySensor):
    """Charging enabled sensor for one specific EV car."""
    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator, entry, car_index: int, car_name: str):
        super().__init__(coordinator, entry)
        self._car_index = car_index
        self._attr_unique_id = f"sem_ev_car_{car_index}_enabled"
        self._attr_name = f"{car_name} Charging Enabled"

    @property
    def native_value(self):
        d = self.coordinator.last_decision
        if not d or self._car_index >= len(d.ev_decisions):
            return "off"
        return "on" if d.ev_decisions[self._car_index].enable else "off"


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
    """Visar om legionelladesinficering pågår."""
    _attr_unique_id = "sem_legionella_active"
    _attr_name = "Legionella Disinfection Active"
    _attr_icon = "mdi:bacteria"

    @property
    def native_value(self) -> str:
        d = self.coordinator.data
        return "on" if (d and d.get("legionella_active")) else "off"


class SmartEnergyLegionellaDaysSinceSensor(_BaseEnergySensor):
    """Dagar sedan senaste legionellakörning."""
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
    """Datum för nästa planerade legionellakörning."""
    _attr_unique_id = "sem_legionella_next_due"
    _attr_name = "Legionella Next Due"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-alert"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("legionella_next_due") if d else None


class SmartEnergyHouseLoadSensor(_BaseEnergySensor):
    """Huslast i W – Elm4 direkt om konfigurerat, annars beräknad."""
    _attr_unique_id = "sem_house_load"
    _attr_name = "House Load"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-lightning-bolt"

    @property
    def native_value(self):
        s = self.coordinator.current_state
        if not s:
            return 0
        return round(s.house_load_w)


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
