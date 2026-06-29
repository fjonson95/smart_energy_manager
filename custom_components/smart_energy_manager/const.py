"""Constants for Smart Energy Manager."""

DOMAIN = "smart_energy_manager"
PLATFORMS = ["sensor", "switch", "number", "select"]

# ── Nätmätare & prissättning ──────────────────────────────────────────────────
CONF_NORDPOOL_ENTITY = "nordpool_entity"
CONF_SOLCAST_TODAY = "solcast_today_entity"
CONF_SOLCAST_TOMORROW = "solcast_tomorrow_entity"

CONF_GRID_POWER_L1 = "grid_power_l1_entity"
CONF_GRID_POWER_L2 = "grid_power_l2_entity"
CONF_GRID_POWER_L3 = "grid_power_l3_entity"
CONF_GRID_CURRENT_L1 = "grid_current_l1_entity"
CONF_GRID_CURRENT_L2 = "grid_current_l2_entity"
CONF_GRID_CURRENT_L3 = "grid_current_l3_entity"
CONF_MAX_CURRENT_PER_PHASE = "max_current_per_phase"
CONF_GRID_VOLTAGE = "grid_voltage"
CONF_GRID_FEES = "grid_fees_sek_kwh"
CONF_ENERGY_TAX = "energy_tax_sek_kwh"
CONF_VAT_RATE = "vat_rate"
CONF_SELL_EXTRA_REVENUE = "sell_extra_revenue"
CONF_HOUSE_LOAD_ENTITY = "house_load_entity"
CONF_GRID_POWER_UNIT = "grid_power_unit"
CONF_EV_POWER_UNIT = "ev_power_unit"

# ── Solceller ─────────────────────────────────────────────────────────────────
CONF_SOLAR_INVERTER_TOTAL = "solar_power_total_entity"
CONF_SOLAR_INVERTER_POWER_L1 = "solar_power_l1_entity"
CONF_SOLAR_INVERTER_POWER_L2 = "solar_power_l2_entity"
CONF_SOLAR_INVERTER_POWER_L3 = "solar_power_l3_entity"

# ── Batteri ───────────────────────────────────────────────────────────────────
CONF_BATTERY_INVERTER_POWER = "battery_inverter_power_entity"
CONF_BATTERY_INVERTER_CHARGE = "battery_inverter_charge_entity"
CONF_BATTERY_INVERTER_DISCHARGE = "battery_inverter_discharge_entity"
CONF_BATTERY_SOC = "battery_soc_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_BATTERY_MAX_POWER_KW = "battery_max_power_kw"
CONF_BATTERY_MIN_SOC = "battery_min_soc"
CONF_BATTERY_MAX_SOC = "battery_max_soc"

# ── Elpanna / värmepump ───────────────────────────────────────────────────────
CONF_HEAT_PUMP_POWER = "heat_pump_power_entity"
CONF_HEAT_PUMP_SWITCH = "heat_pump_switch_entity"
CONF_HEAT_PUMP_EXTRA_HOT_WATER = "heat_pump_extra_hot_water_entity"
CONF_HEAT_PUMP_PHASE = "heat_pump_phase"
CONF_HEAT_PUMP_PATRON_PHASES = "heat_pump_patron_phases"
CONF_HEAT_PUMP_PATRON_POWER_KW = "heat_pump_patron_power_kw"

# ── EV-laddare (ny modell) ────────────────────────────────────────────────────
# Lista av laddare; varje laddare har en lista av möjliga bilar.
#
# Laddare-dict:
#   {
#     "name": str,                     # visningsnamn
#     "connected_sensor": str | None,  # sensor som visar connected/charging/disconnected
#     "charger_switch": str,           # switch för att aktivera/avaktivera laddning
#     "charger_current": str,          # number-entitet för strömsättning
#     "charger_power": str | None,     # sensor för faktisk laddeffekt (valfri)
#     "phases": int,                   # 1 eller 3 (hårdvara)
#     "phase": str | None,             # "L1"/"L2"/"L3" – only when phases==1
#     "cars": [                        # lista av bilar som kan använda denna laddare
#       {
#         "name": str,
#         "ev_soc": str | None,        # SOC-sensor
#         "ev_soc_target": float,      # mål-SOC %
#         "phase": str | None,         # fas bilen laddar på (vid 1-fas laddare)
#       },
#       ...
#     ]
#   }
CONF_EV_CHARGERS = "ev_chargers"

# Bakåtkompatibilitet – gamla nyckeln
CONF_EV_CARS = "ev_cars"

# Legacy single-car keys (kept for migration)
CONF_EV_CHARGER_POWER = "ev_charger_power_entity"
CONF_EV_CHARGER_SWITCH = "ev_charger_switch_entity"
CONF_EV_CHARGER_CURRENT = "ev_charger_current_entity"
CONF_EV_CHARGER_PHASES = "ev_charger_phases"
CONF_EV_CHARGER_PHASE = "ev_charger_phase"
CONF_EV_SOC = "ev_soc_entity"
CONF_EV_SOC_TARGET = "ev_soc_target"

# ── Winter mode ───────────────────────────────────────────────────────────────
CONF_WINTER_MODE_ENABLED = "winter_mode_enabled"
CONF_WINTER_CHEAP_HOUR_THRESHOLD = "winter_cheap_hour_threshold"
CONF_WINTER_EXPENSIVE_HOUR_THRESHOLD = "winter_expensive_hour_threshold"
CONF_WINTER_MIN_SOC = "winter_min_soc"
CONF_WINTER_MAX_SOC = "winter_max_soc"

# ── Legionella ────────────────────────────────────────────────────────────────
CONF_LEGIONELLA_ENABLED = "legionella_enabled"
CONF_LEGIONELLA_INTERVAL_DAYS = "legionella_interval_days"
CONF_LEGIONELLA_PREFERRED_HOUR_START = "legionella_preferred_hour_start"
CONF_LEGIONELLA_PREFERRED_HOUR_END = "legionella_preferred_hour_end"
CONF_LEGIONELLA_MAX_PRICE = "legionella_max_price_sek_kwh"
CONF_LEGIONELLA_DURATION_MINUTES = "legionella_duration_minutes"
CONF_LEGIONELLA_LAST_RUN = "legionella_last_run"

# ── Standardvärden ────────────────────────────────────────────────────────────
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

DEFAULT_HEAT_PUMP_PHASE = "L3"
DEFAULT_HEAT_PUMP_PATRON_PHASES = ["L1", "L2"]
DEFAULT_HEAT_PUMP_PATRON_POWER_KW = 6.0

DEFAULT_LEGIONELLA_ENABLED = True
DEFAULT_LEGIONELLA_INTERVAL_DAYS = 7
DEFAULT_LEGIONELLA_PREFERRED_HOUR_START = 10
DEFAULT_LEGIONELLA_PREFERRED_HOUR_END = 15
DEFAULT_LEGIONELLA_MAX_PRICE = 1.50
DEFAULT_LEGIONELLA_DURATION_MINUTES = 60

# ── Fas-alternativ ────────────────────────────────────────────────────────────
EV_PHASE_L1 = "L1"
EV_PHASE_L2 = "L2"
EV_PHASE_L3 = "L3"
EV_PHASES_OPTIONS = [EV_PHASE_L1, EV_PHASE_L2, EV_PHASE_L3]
EV_NUM_PHASES_OPTIONS = [1, 3]
PHASES = ["L1", "L2", "L3"]

# ── Driftlägen ────────────────────────────────────────────────────────────────
MODE_AUTO = "auto"
MODE_WINTER = "winter"
MODE_FORCE_CHARGE_EV = "force_charge_ev"
MODE_FORCE_CHARGE_BATTERY = "force_charge_battery"
MODE_MANUAL = "manual"
OPERATING_MODES = [MODE_AUTO, MODE_WINTER, MODE_FORCE_CHARGE_EV, MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL]

# ── Laddarsensorvärden som räknas som "ansluten" ──────────────────────────────
CHARGER_CONNECTED_STATES = {"connected", "charging", "plugged_in", "pluggedin", "waiting",
                             "ready", "preparing", "suspended_ev", "suspended_evse"}

# Sentinel för "ingen bil vald" på en laddare
NO_CAR_SELECTED = "unknown"

# ── Övrigt ────────────────────────────────────────────────────────────────────
UPDATE_INTERVAL = 30
MIN_SOLAR_FOR_EV_1PHASE = 1400
MIN_SOLAR_FOR_EV_3PHASE = 4140
MIN_EV_CURRENT = 6
MAX_EV_CURRENT = 16
NEGATIVE_PRICE_THRESHOLD = 0.0
UNIT_W = "W"
UNIT_KW = "kW"

# ── Ackumulatortank & varmvattenstyrning ──────────────────────────────────────
CONF_HOT_WATER_TEMP_ENTITY = "hot_water_temp_entity"
CONF_LEGIONELLA_SWITCH = "legionella_switch_entity"
CONF_EXTRA_HOT_WATER_MAX_TEMP = "extra_hot_water_max_temp"
CONF_LEGIONELLA_TARGET_TEMP = "legionella_target_temp"

DEFAULT_EXTRA_HOT_WATER_MAX_TEMP = 70.0   # °C – stoppa extra varmvatten över denna nivå
DEFAULT_LEGIONELLA_TARGET_TEMP = 65.0     # °C – bekräfta legionella klar när temp nått detta
