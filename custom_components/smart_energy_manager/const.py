"""Constants for Smart Energy Manager."""

DOMAIN = "smart_energy_manager"
PLATFORMS = ["sensor", "switch", "number", "select"]

# ── Nätmätare & prissättning ──────────────────────────────────────────────────
CONF_NORDPOOL_ENTITY = "nordpool_entity"
CONF_NORDPOOL_TYPE = "nordpool_type"
NORDPOOL_TYPE_HACS = "hacs"
NORDPOOL_TYPE_OFFICIAL = "official"
CONF_NORDPOOL_AREA = "nordpool_area"
DEFAULT_NORDPOOL_AREA = "SE3"
CONF_SOLCAST_TODAY = "solcast_today_entity"
CONF_SOLCAST_TOMORROW = "solcast_tomorrow_entity"
CONF_ACTUAL_SOLAR_DAILY_ENTITY = "actual_solar_daily_entity"

CONF_GRID_POWER_L1 = "grid_power_l1_entity"
CONF_GRID_POWER_L2 = "grid_power_l2_entity"
CONF_GRID_POWER_L3 = "grid_power_l3_entity"
CONF_GRID_CURRENT_L1 = "grid_current_l1_entity"
CONF_GRID_CURRENT_L2 = "grid_current_l2_entity"
CONF_GRID_CURRENT_L3 = "grid_current_l3_entity"
CONF_MAX_CURRENT_PER_PHASE = "max_current_per_phase"
CONF_MAX_EXPORT_W = "max_export_w"
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
# P4-2 steg 5: strypning vid negativt pris. Kalibrerad mot märkeffekt, se P4-1.
CONF_SOLAR_CURTAILMENT_ENTITY = "solar_curtailment_entity"
CONF_SOLAR_INVERTER_RATED_KW = "solar_inverter_rated_kw"
DEFAULT_SOLAR_INVERTER_RATED_KW = 12.0

# ── Batteri ───────────────────────────────────────────────────────────────────
CONF_BATTERY_INVERTER_POWER = "battery_inverter_power_entity"
CONF_BATTERY_INVERTER_CHARGE = "battery_inverter_charge_entity"
CONF_BATTERY_INVERTER_DISCHARGE = "battery_inverter_discharge_entity"
CONF_BATTERY_OPERATING_MODE_ENTITY = "battery_operating_mode_entity"
CONF_BATTERY_SOC = "battery_soc_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_BATTERY_MAX_POWER_KW = "battery_max_power_kw"
CONF_BATTERY_MIN_SOC = "battery_min_soc"
CONF_BATTERY_MAX_SOC = "battery_max_soc"
# Ackumulerade AC-ur-/urladdningsräknare (kWh, total_increasing) – valfria.
# Om satta: (a) grund för sem_battery_equivalent_cycles (v1.0 steg 1, se
# docs/v1_implementation_plan.md), (b) samma källa eta_roundtrip kalibrerades
# mot 2026-09-08 (0.849, uppmätt mot dessa räknare på just den här
# anläggningen – gäller INTE generellt för andra batterier/config-defaulten).
CONF_BATTERY_AC_DISCHARGE_ENERGY_ENTITY = "battery_ac_discharge_energy_entity"
CONF_BATTERY_AC_CHARGE_ENERGY_ENTITY = "battery_ac_charge_energy_entity"

# ── Elpanna / värmepump ───────────────────────────────────────────────────────
CONF_HEAT_PUMP_POWER = "heat_pump_power_entity"
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
DEFAULT_MAX_EXPORT_W = 14000.0     # Växelriktarens AC-exportgräns (skilt från fasströmsgränsen)
DEFAULT_PHASE_CURRENT_MARGIN = 2.0  # A – marginal mot fasgränsen (trög 30s-reglering + Sonnen-fördröjning)
DEFAULT_GRID_VOLTAGE = 230
DEFAULT_VAT_RATE = 0.25
DEFAULT_GRID_FEES = 0.45
DEFAULT_ENERGY_TAX = 0.536
DEFAULT_SELL_EXTRA_REVENUE = 0.065  # Nätnytta, Lerum Energi (6,50 öre inkl. moms) – uppmätt (v1.0 steg 1)
DEFAULT_BATTERY_MIN_SOC = 10
DEFAULT_BATTERY_MAX_SOC = 95

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
MODE_FORCE_CHARGE_EV = "force_charge_ev"
MODE_FORCE_CHARGE_BATTERY = "force_charge_battery"
MODE_MANUAL = "manual"
OPERATING_MODES = [MODE_AUTO, MODE_FORCE_CHARGE_EV, MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL]

# ── Laddarsensorvärden som räknas som "ansluten" ──────────────────────────────
CHARGER_CONNECTED_STATES = {"connected", "charging", "plugged_in", "pluggedin", "waiting",
                             "ready", "preparing", "suspended_ev", "suspended_evse","ev connected"}

# Sentinel för "ingen bil vald" på en laddare. INTE "unknown" – det krockar med
# Home Assistants eget reserverade tillstånd för saknad data (STATE_UNKNOWN),
# vilket får select-entiteten att se ut som att den inte har ett värde alls.
NO_CAR_SELECTED = "none"

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
CONF_EXTRA_HOT_WATER_MIN_TEMP = "extra_hot_water_min_temp"
CONF_LEGIONELLA_TARGET_TEMP = "legionella_target_temp"

DEFAULT_EXTRA_HOT_WATER_MAX_TEMP = 70.0   # °C – stoppa extra varmvatten över denna nivå
DEFAULT_EXTRA_HOT_WATER_MIN_TEMP = 65.0   # °C – starta inte extra varmvatten förrän temp är under detta
DEFAULT_LEGIONELLA_TARGET_TEMP = 65.0     # °C – bekräfta legionella klar när temp nått detta

# ── Prisplanering ─────────────────────────────────────────────────────────────
CONF_YESTERDAY_CONSUMPTION_ENTITY = "yesterday_consumption_entity"
CONF_OUTDOOR_TEMP_ENTITY = "outdoor_temp_entity"
# Dämpad (utjämnad) utetemp – bättre proxy för värmebehov än momentan avläsning.
# Valfri: om satt, ersätter den momentana temp_for_model-uppbyggnaden helt.
CONF_DAMPED_OUTDOOR_TEMP_ENTITY = "damped_outdoor_temp_entity"

# ── Förbrukningsprognos ───────────────────────────────────────────────────────
# Modell: predicted_kwh = base_dhw + k_heat * max(0, T_balance - temp)
# Kalibrerade mot helårsstatistik okt 2025–sep 2026 (kompressor-andel =
# boiler_nrgconstotal-delta minus auxelecheatnrgconstotal-delta, mot
# thermostat_dampedoutdoortemp), se docs/forbrukningsanalys.md avsnitt 7.
# Ersätter den tidigare kalibreringen (nov 2025–jul 2026, k=1.275) som kraftigt
# underskattade förbrukningen vid riktig kyla (t.ex. -7,8°C: gamla modellen
# ~29 kWh mot uppmätt 63 kWh).
CONF_HEAT_BALANCE_TEMP       = "heat_balance_temp"
CONF_HEAT_FACTOR_KWH_DD      = "heat_factor_kwh_dd"
CONF_BASE_DHW_KWH            = "base_dhw_kwh"
CONF_DISINFECTING_EXTRA_KWH  = "disinfecting_extra_kwh"
DEFAULT_HEAT_BALANCE_TEMP    = 12.0   # °C – balanstemperatur (uppvärmning startar under denna)
DEFAULT_HEAT_FACTOR_KWH_DD   = 2.39   # kWh per gradddag
DEFAULT_BASE_DHW_KWH         = 1.0    # kWh/dag – fast varmvattenbas
DEFAULT_DISINFECTING_EXTRA_KWH = 5.0  # kWh extra vid desinficering/legionella

# EV-marginal i exportgolvet: flat säkerhetsbuffert (inte hela vägen till
# soc_target) när en bil är vald och kan behöva ladda under ett mörkt/lågsol-
# fönster. Se energy_planner.py::build_plan().
CONF_EV_RESERVE_MARGIN_KWH   = "ev_reserve_margin_kwh"
DEFAULT_EV_RESERVE_MARGIN_KWH = 2.5
CONF_NEGATIVE_PRICE_THRESHOLD = "negative_price_threshold_sek"
CONF_PROACTIVE_ABSORPTION_SLOTS = "proactive_absorption_slots"
CONF_EXPORT_SELL_PERCENTILE = "export_sell_percentile"
CONF_EXPORT_MIN_SOLAR_TOMORROW_KWH = "export_min_solar_tomorrow_kwh"
CONF_EXPORT_MIN_SELL_PRICE_SEK_KWH = "export_min_sell_price_sek_kwh"
CONF_BATTERY_POWER_INVERTED = "battery_power_inverted"
CONF_ETA_ROUNDTRIP = "eta_roundtrip"
CONF_CYCLE_COST_SEK_KWH = "cycle_cost_sek_kwh"

DEFAULT_NEGATIVE_PRICE_THRESHOLD = 0.0   # SEK/kWh
DEFAULT_PROACTIVE_ABSORPTION_SLOTS = 4   # Antal negativa kvartstimmar inom 2h för att reagera
DEFAULT_EXPORT_SELL_PERCENTILE = 0.75    # Urladda/exportera när säljpris ≥ 75:e percentilen av dagens priser
DEFAULT_EXPORT_MIN_SOLAR_TOMORROW_KWH = 5.0  # Minsta Solcast-prognos imorgon för att tillåta export-urladdning
DEFAULT_EXPORT_MIN_SELL_PRICE_SEK_KWH = 0.70  # Absolut minimipris – exporterar alltid om ≥ detta (oavsett percentil)
DEFAULT_ETA_ROUNDTRIP = 0.849            # Batteriets tur-och-retur-verkningsgrad – uppmätt (v1.0 steg 1):
                                          # sensor.sonnen_batt_use_energy / sensor.sonnen_battcharge_energy,
                                          # anläggningens egna AC-räknare, inte en antagen siffra
DEFAULT_CYCLE_COST_SEK_KWH = 0.05        # Uppskattad slitagekostnad per cyklad kWh

DEFAULT_CHEAP_CHARGE_MAX_SOLAR_KWH = 10.0   # Opportunistisk laddning tillåts om sol imorgon < detta (kWh)
DEFAULT_CHEAP_CHARGE_BUY_PERCENTILE = 0.30  # Ladda under billigaste 30% av kommande prisslots

# ── Minimitid för extra varmvatten ──────────────────────────────────────────────
CONF_EXTRA_HOT_WATER_MIN_RUNTIME_MINUTES = "extra_hot_water_min_runtime_minutes"
DEFAULT_EXTRA_HOT_WATER_MIN_RUNTIME_MINUTES = 5
