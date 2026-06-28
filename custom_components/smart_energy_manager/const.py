"""Constants for Smart Energy Manager."""

DOMAIN = "smart_energy_manager"
PLATFORMS = ["sensor", "switch", "number", "select"]

# Config keys
CONF_BATTERY_INVERTER_POWER = "battery_inverter_power_entity"
CONF_BATTERY_INVERTER_CHARGE = "battery_inverter_charge_entity"
CONF_BATTERY_INVERTER_DISCHARGE = "battery_inverter_discharge_entity"
CONF_BATTERY_SOC = "battery_soc_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_BATTERY_MAX_POWER_KW = "battery_max_power_kw"

CONF_SOLAR_INVERTER_POWER_L1 = "solar_power_l1_entity"
CONF_SOLAR_INVERTER_POWER_L2 = "solar_power_l2_entity"
CONF_SOLAR_INVERTER_POWER_L3 = "solar_power_l3_entity"
CONF_SOLAR_INVERTER_TOTAL = "solar_power_total_entity"

# Multi-car EV charger configuration
# Each car entry is a dict:
#   {
#     "name": str,                        # friendly name
#     "charger_switch": str,              # switch entity to enable/disable
#     "charger_current": str,             # number entity for current setpoint
#     "charger_power": str | None,        # sensor entity for actual power (optional)
#     "ev_soc": str | None,               # sensor for EV SOC (optional)
#     "ev_soc_target": float,             # target SOC % (default: 80)
#     "phases": int,                      # 1 or 3
#     "phase": str | None,                # "L1"/"L2"/"L3" – only when phases==1
#   }
CONF_EV_CARS = "ev_cars"

# Legacy single-car keys (kept for backwards compatibility in migration)
CONF_EV_CHARGER_POWER = "ev_charger_power_entity"
CONF_EV_CHARGER_SWITCH = "ev_charger_switch_entity"
CONF_EV_CHARGER_CURRENT = "ev_charger_current_entity"
CONF_EV_CHARGER_PHASES = "ev_charger_phases"
CONF_EV_CHARGER_PHASE = "ev_charger_phase"
CONF_EV_SOC = "ev_soc_entity"
CONF_EV_SOC_TARGET = "ev_soc_target"

# Heat pump / boiler
# The physical unit has two distinct operating circuits:
#   1. Heat pump compressor  – always 1-phase (phase configurable, default L3)
#   2. Electric heating element (patron) – always 2-phase (phases configurable,
#      default L1+L2) used only for "extra hot water" mode
CONF_HEAT_PUMP_POWER = "heat_pump_power_entity"
CONF_HEAT_PUMP_SWITCH = "heat_pump_switch_entity"
CONF_HEAT_PUMP_EXTRA_HOT_WATER = "heat_pump_extra_hot_water_entity"
CONF_HEAT_PUMP_PHASE = "heat_pump_phase"                # L1 / L2 / L3  (compressor, 1-phase)
CONF_HEAT_PUMP_PATRON_PHASES = "heat_pump_patron_phases"  # list[str] e.g. ["L1","L2"] (element, 2-phase)
CONF_HEAT_PUMP_PATRON_POWER_KW = "heat_pump_patron_power_kw"  # rated kW of heating element

CONF_GRID_POWER_L1 = "grid_power_l1_entity"
CONF_GRID_POWER_L2 = "grid_power_l2_entity"
CONF_GRID_POWER_L3 = "grid_power_l3_entity"
CONF_GRID_CURRENT_L1 = "grid_current_l1_entity"
CONF_GRID_CURRENT_L2 = "grid_current_l2_entity"
CONF_GRID_CURRENT_L3 = "grid_current_l3_entity"

CONF_NORDPOOL_ENTITY = "nordpool_entity"
CONF_SOLCAST_ENTITY = "solcast_entity"
CONF_SOLCAST_TODAY = "solcast_today_entity"
CONF_SOLCAST_TOMORROW = "solcast_tomorrow_entity"

CONF_GRID_FEES = "grid_fees_sek_kwh"          # Nätavgifter öre/kWh
CONF_ENERGY_TAX = "energy_tax_sek_kwh"        # Energiskatt öre/kWh
CONF_VAT_RATE = "vat_rate"                     # Moms (default 0.25)
CONF_SELL_EXTRA_REVENUE = "sell_extra_revenue" # Elcertifikat etc öre/kWh

CONF_MAX_CURRENT_PER_PHASE = "max_current_per_phase"  # Default 20A
CONF_GRID_VOLTAGE = "grid_voltage"                     # Default 230V

CONF_WINTER_MODE_ENABLED = "winter_mode_enabled"
CONF_WINTER_CHEAP_HOUR_THRESHOLD = "winter_cheap_hour_threshold"
CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD = "winter_expensive_hour_threshold"
CONF_WINTER_MIN_SOC = "winter_min_soc"
CONF_WINTER_MAX_SOC = "winter_max_soc"
CONF_BATTERY_MIN_SOC = "battery_min_soc"
CONF_BATTERY_MAX_SOC = "battery_max_soc"

# Default values
DEFAULT_MAX_CURRENT = 20
DEFAULT_GRID_VOLTAGE = 230
DEFAULT_VAT_RATE = 0.25
DEFAULT_GRID_FEES = 0.45
DEFAULT_ENERGY_TAX = 0.536
DEFAULT_SELL_EXTRA_REVENUE = 0.07
DEFAULT_BATTERY_MIN_SOC = 10
DEFAULT_BATTERY_MAX_SOC = 95
DEFAULT_WINTER_MIN_SOC = 20
DEFAULT_WINTER_MAX_SOC = 95
DEFAULT_EV_SOC_TARGET = 80
DEFAULT_WINTER_CHEAP_THRESHOLD = 0.80
DEFAULT_WINTER_EXPENSIVE_THRESHOLD = 1.50

# Heat pump defaults
DEFAULT_HEAT_PUMP_PHASE = "L3"                         # Compressor (1-phase)
DEFAULT_HEAT_PUMP_PATRON_PHASES = ["L1", "L2"]         # Heating element (2-phase)
DEFAULT_HEAT_PUMP_PATRON_POWER_KW = 6.0                # Typical 6 kW element → 3 kW/phase

# EV phase options
EV_PHASE_L1 = "L1"
EV_PHASE_L2 = "L2"
EV_PHASE_L3 = "L3"
EV_PHASES_OPTIONS = [EV_PHASE_L1, EV_PHASE_L2, EV_PHASE_L3]
EV_NUM_PHASES_OPTIONS = [1, 3]

# Operating modes
MODE_AUTO = "auto"
MODE_WINTER = "winter"
MODE_FORCE_CHARGE_EV = "force_charge_ev"
MODE_FORCE_CHARGE_BATTERY = "force_charge_battery"
MODE_MANUAL = "manual"
OPERATING_MODES = [MODE_AUTO, MODE_WINTER, MODE_FORCE_CHARGE_EV, MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL]

# Update interval seconds
UPDATE_INTERVAL = 30

# Minimum solar surplus to consider starting EV charging
MIN_SOLAR_FOR_EV_1PHASE = 1400   # W  (~6 A × 230 V)
MIN_SOLAR_FOR_EV_3PHASE = 4140   # W  (~6 A × 230 V × 3 phases)
MIN_EV_CURRENT = 6               # A  (IEC 61851 minimum)
MAX_EV_CURRENT = 32              # A

# Negative price threshold
NEGATIVE_PRICE_THRESHOLD = 0.0  # SEK/kWh

# Phase labels
PHASES = ["L1", "L2", "L3"]
# House load direct measurement (e.g. Elm4 – fastigheten exkl. sol/batteri)
# Om konfigurerad används denna direkt istället för att beräkna från nätmätaren.
CONF_HOUSE_LOAD_ENTITY = "house_load_entity"

# Enhetsskalning – vissa mätare rapporterar i kW istället för W
CONF_GRID_POWER_UNIT = "grid_power_unit"   # "W" (default) eller "kW"
CONF_EV_POWER_UNIT = "ev_power_unit"       # "W" (default) eller "kW"

UNIT_W = "W"
UNIT_KW = "kW"
