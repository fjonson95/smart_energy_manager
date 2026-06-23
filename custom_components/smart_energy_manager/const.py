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

CONF_EV_CHARGER_POWER = "ev_charger_power_entity"
CONF_EV_CHARGER_SWITCH = "ev_charger_switch_entity"
CONF_EV_CHARGER_CURRENT = "ev_charger_current_entity"
CONF_EV_CHARGER_PHASES = "ev_charger_phases"
CONF_EV_CHARGER_PHASE = "ev_charger_phase"  # Which phase single-phase car uses (L1/L2/L3)
CONF_EV_SOC = "ev_soc_entity"
CONF_EV_SOC_TARGET = "ev_soc_target"

CONF_HEAT_PUMP_POWER = "heat_pump_power_entity"
CONF_HEAT_PUMP_SWITCH = "heat_pump_switch_entity"
CONF_HEAT_PUMP_EXTRA_HOT_WATER = "heat_pump_extra_hot_water_entity"

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
DEFAULT_GRID_FEES = 0.45       # SEK/kWh
DEFAULT_ENERGY_TAX = 0.536     # SEK/kWh
DEFAULT_SELL_EXTRA_REVENUE = 0.07  # SEK/kWh
DEFAULT_BATTERY_MIN_SOC = 10
DEFAULT_BATTERY_MAX_SOC = 95
DEFAULT_WINTER_MIN_SOC = 20
DEFAULT_WINTER_MAX_SOC = 95
DEFAULT_EV_SOC_TARGET = 80
DEFAULT_WINTER_CHEAP_THRESHOLD = 0.80   # SEK/kWh
DEFAULT_WINTER_EXPENSIVE_THRESHOLD = 1.50  # SEK/kWh

# EV charger phase options
EV_PHASE_L1 = "L1"
EV_PHASE_L2 = "L2"
EV_PHASE_L3 = "L3"
EV_PHASES_OPTIONS = [EV_PHASE_L1, EV_PHASE_L2, EV_PHASE_L3]

# Operating modes
MODE_AUTO = "auto"
MODE_WINTER = "winter"
MODE_FORCE_CHARGE_EV = "force_charge_ev"
MODE_FORCE_CHARGE_BATTERY = "force_charge_battery"
MODE_MANUAL = "manual"
OPERATING_MODES = [MODE_AUTO, MODE_WINTER, MODE_FORCE_CHARGE_EV, MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL]

# Update interval seconds
UPDATE_INTERVAL = 30

# Minimum solar power to consider charging EV (W)
MIN_SOLAR_FOR_EV = 1400   # ~6A on 1 phase
MIN_EV_CURRENT = 6        # A (IEC 61851 minimum)
MAX_EV_CURRENT = 32       # A

# Negative price threshold
NEGATIVE_PRICE_THRESHOLD = 0.0  # SEK/kWh

# Phase numbering
PHASES = ["L1", "L2", "L3"]
