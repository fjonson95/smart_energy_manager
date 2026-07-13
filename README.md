# Smart Energy Manager – HACS Integration

![Version](https://img.shields.io/badge/version-0.5.8-blue)

A HACS integration for Home Assistant that optimizes self-consumption of solar energy with battery, EV charger, and electric boiler/water heater.

Läs detta på svenska: [README.sv.md](https://github.com/fjonson95/smart_energy_manager/blob/main/README.sv.md)

## What's New in 0.5.9

- **Proactive export: absolute minimum sell price** – added a new Battery setting `export_min_sell_price_sek_kwh` (default 0.70 SEK/kWh). When the sell price meets or exceeds this threshold, the battery discharges and exports to the grid regardless of the relative percentile position. This fixes the case where all hours of the day have uniformly high prices, causing the percentile threshold to be too high to trigger even at genuinely profitable sell prices. Set to 0 to disable.
- **Proactive export: today-only percentile** – the price percentile used to decide whether to export is now calculated from today's price slots only (previously it included tomorrow's prices, which could inflate the threshold when both days had high prices).

## What's New in 0.5.8

- **Fix: config/options UI labels** – all config flow steps now have correct labels in both English and Swedish. The charger and car setup steps (`charger_menu`, `charger`, `car_menu`, `car` and their options counterparts) previously used wrong step IDs (`ev_menu`/`ev_car`) that did not match the actual flow, causing fields to show raw key names instead of readable labels. All fields — charger name, connection sensor, charge current setpoint, car onboard charger phases, etc. — now display correctly.
- **Backtest framework** – added `testdata/backtest.py` for replaying historical sensor data through the energy controller. Reads timdata/realtidsdata CSVs, reconstructs `EnergyState` at each timestamp, runs `EnergyController.compute()`, and outputs a semicolon-separated CSV with comma as decimal separator (Excel-compatible). Supports date filtering and parameter overrides (`--min-soc`, `--percentile`).

## What's New in 0.5.7

- **Battery cost accounting** – three new sensors track the accumulated cost of energy currently stored in the battery:
  - `sensor.sem_battery_accumulated_cost` – total cost (SEK) of energy in the battery. Grid energy is priced at the current buy price; solar energy is priced at the current sell price (opportunity cost – you could have sold it instead). During discharge the cost is reduced proportionally.
  - `sensor.sem_battery_average_price` – average cost per kWh currently in the battery (SEK/kWh). Always derived directly from the accumulated cost sensor to stay consistent.
  - Diagnostic attributes on the cost sensor: `solar_kwh_total`, `grid_kwh_total`, `last_sell_price`.
- **Fix: battery power sign inversion** – added a configurable option "Inverted battery power sign" in the Battery settings (enable for Sonnenbatterie, which reports positive = discharging). Without this option the accumulated cost sensor compounded upward with every charge/discharge cycle, producing unrealistically high average prices. After enabling this setting, reset the counter using the new service `smart_energy_manager.reset_battery_cost` via Developer Tools → Services.
- **Proactive export** – battery discharges and exports to grid when the current sell price is at or above a configurable percentile of today's prices, provided tomorrow's Solcast forecast is above a minimum threshold. Configurable in Battery settings (`export_sell_percentile`, `export_min_solar_tomorrow_kwh`).
- **Proactive absorption revised** – when negative electricity prices are expected within 2 h, the system now only holds battery headroom (up to 30%). Extra hot water and EV pre-charging are **no longer started proactively** – they wait until the price is actually negative. After a negative price period has passed today, extra hot water is still offered when the boiler needs it.
- **Solar-active battery charge cap** – when solar production exceeds 100 W the battery is never charged beyond the actual solar surplus; grid power is never drawn into the battery while the sun is producing.
- **Discharge blocked when solar covers load** – if solar output already covers the full house load the system no longer forces battery discharge, even at high prices, since no grid power is being purchased anyway.

## What's New in 0.5.6

- **Sell-price-aware battery charging** – when the sell price is at or above a configurable threshold (default SEK 0.80/kWh), solar surplus is exported to the grid instead of stored in the battery.
- **Solcast-based evening fill** – the system compares the remaining Solcast solar forecast until sunset against how much energy the battery still needs to reach the configured minimum SOC (`evening_min_soc`, default 90 %). If the forecast falls short, the battery is charged from solar (or grid) even when sell prices are high. Because the Solcast forecast is calibrated to the actual panel location and angle, this works correctly across all seasons without any fixed clock time.
- **Fix: battery no longer waits for solar during active production** – previously the "wait for solar" flag blocked battery charging all day whenever significant solar was expected within 2 h. The guard now only activates when current solar output is below 500 W (sun not yet producing).
- **Sun integration** – `sun_next_setting` and `sun_next_rising` are read from the `sun.sun` entity and added to `EnergyState` for use in the evening-fill calculation.

## What's New in 0.5.5

- **Solcast 30-minute solar forecast integration** – the `detailedForecast` attribute from Solcast sensors is matched against Nord Pool price slots, giving the scheduler per-slot solar data. New decisions: skip grid battery charging when significant solar is expected within 2 h, and create extra battery headroom ahead of a large solar peak. Six new sensors exposed (see [Entities](#entities)).

## What's New in 0.5.4

- **Auto-clear active car after full charge** – when a car reaches its SOC target the active car selection is automatically reset to "unknown", so the charger is ready for the next session without manual intervention.

## System Overview

```
Grid (3-phase, max 20A/phase)
    │
    │    Battery inverter (3-phase)     Solar inverter (3-phase)
    │           │                             │
    └─────┬─────┴─────────────────────────────┴──── Meter 4 (house load) ────┬──── Other loads
          │                                                                  │
          │                                                              Meter 5
          │                                                                   │
          │                                                                Boiler
          │                                                              (1-phase compressor
          │                                                             +2-phase heating element
          └────┐                                                        +buffer tank)
               │
           EV charger
        (1- or 3-phase hardware,
         multiple cars take turns,
         car selection via HA entity)
```

> **NOTE:** The charger's number of phases (hardware) and the car's number of phases (`car_phases`, 1/2/3-phase) are two separate settings. Phase load in the phase protection is always governed by the **car's** built-in charger, not by the charger hardware's phase count – see [EV Charger and Car Selection](#ev-charger-and-car-selection).

### Electricity Meter Roles

| Meter | Location | Sign | Unit |
|---|---|---|---|
| Meter 1 | Grid connection | Negative = export, Positive = import | kW |
| Meter 2 | Battery inverter AC side | Negative = consumption | W |
| Meter 3 | Solar inverter | Positive = production | W |
| Meter 4 | Property load (excl. solar, battery, and EV charger) | Positive = consumption | W |
| Meter 5 | Boiler | Positive = consumption | W |

> **NOTE:** Meter 1 reports in **kW** – select the unit `kW` in the configuration. The EV charger (internal meter) also reports in **kW**.

---

## Features

### Auto Mode (Self-Consumption)
1. **Cover household load** – priority 1
2. **Charge cars from solar** – when solar surplus ≥ 1,400 W (1-phase) or 4,140 W (3-phase); current adjusted dynamically
3. **Charge battery from solar surplus**
4. **Extra hot water via heating element** – when the battery is full, solar surplus remains, and tank temperature is below the configured maximum (default 70°C)
5. **Discharge the battery** – when the buy price exceeds SEK 0.20/kWh, *or* when it is the best discharge hour in the coming 12h according to the price planning
6. **Negative electricity prices** – absorb all possible solar power into battery, cars, and hot water

### Price Planning (quarter-hours ahead)
Based on Nord Pool's `raw_today`/`raw_tomorrow` attributes, the following are calculated each cycle:
- **Best charging/discharging hour** for the coming 12h – governs battery decisions in both auto and winter mode
- **Proactive absorption** – if ≥ 4 quarter-hours with negative sell price are expected within 2h, the battery holds headroom (up to 30%) to make room for solar. Extra hot water and EV charging are **not** started proactively; they wait until the price is actually negative
- Results are exposed via `sensor.sem_negative_slots_ahead`, `sensor.sem_best_discharge_price`, and `sensor.sem_best_charge_price`

### Solcast Solar Forecast (30-minute resolution)
If Solcast sensors are configured, the `detailedForecast` attribute (30-minute `pv_estimate` values in kW) is read each cycle and matched against Nord Pool price slots:
- **Per-slot solar data** – each price slot gets an expected solar power value (kW) and energy (kWh)
- **Wait for solar** – if ≥ 2 kWh of solar is expected within 2 h *and* the current buy price is above SEK 0.50/kWh, battery charging from the grid is skipped to preserve headroom for free solar
- **Solar aggregates** – rolling forecasts for the next 2 h, 4 h, and 8 h exposed as sensors
- **Peak solar** – the expected peak power (kW) within 8 h and how many hours until it occurs
- `sensor.sem_wait_for_solar` turns `on` when the system is holding back grid charging in anticipation of solar

### Winter Mode
- Charge battery overnight when the price is below a configurable limit
- Discharge battery during expensive hours (evening peak)
- Always charge from solar when possible

### Force Modes
- **Force EV Charge** – charge all connected cars from the grid (max current, phase-limited)
- **Force Battery Charge** – charge battery from the grid

### Manual Mode
- **Manual** – disables all automatic control. No decisions are made; used when you want to control battery/charger/hot water manually via your own automations. Selected via `select.sem_operating_mode` (there is no dedicated switch for this mode).

### Phase Protection
Calculates phase load per phase and reduces in priority order:
1. Reduces EV charging current (lowest-priority charger first)
2. Reduces battery charging
3. Turns off extra hot water (heating element)

---

## EV Charger and Car Selection

### Charger → Cars Model

Each EV charger is configured as a **hardware unit** with a list of cars that can use it. When a car is connected, the user selects which car it is via a `select` entity in the dashboard.

```
Charger A (3-phase hardware)
├── Connection sensor: sensor.charger_status
├── Car 1: Volvo XC40    (SOC sensor, target 80%, 1-phase car charger, phase L1)
└── Car 2: Tesla Model 3 (SOC sensor, target 90%, 3-phase car charger)
```

The phase load that the phase protection calculates is always determined by the **car's** `car_phases` (1/2/3-phase), not by the charger hardware's phase count. A 1-phase car only loads its selected phase; a 2-phase car loads its phase plus the next one (e.g., L1+L2); a 3-phase car loads all three phases equally.

### Car Selection Flow

1. Car is connected → `sensor.sem_charger_a_connected` → `on`
2. The system pauses charging and sends a persistent HA notification
3. The user selects the car in `select.sem_charger_a_active_car`
4. The system charges using the correct SOC target and phase setting for the selected car
5. The notification closes automatically

### Configuration per Charger

| Setting | Description |
|---|---|
| Name | Display name for the charger |
| Connection sensor | Sensor showing `connected`/`charging`/`disconnected` |
| Charger switch | Switch to enable/disable charging |
| Charging current setpoint | Number entity for current setting (A) |
| Charging power sensor | Sensor for actual charging power (optional) |
| Number of phases | 1-phase or 3-phase (hardware) |

### Configuration per Car

| Setting | Description |
|---|---|
| Name | Display name for the car |
| SOC sensor | The car's battery sensor (optional) |
| Target SOC | Charging target in % (default 80%) |
| Number of phases (car's charger) | 1-phase, 2-phase, or 3-phase – the car's **built-in** charger, governs phase load |
| Phase | Starting phase the car charges on (relevant for 1- and 2-phase cars) |

---

## Electric Boiler – Phase Model and Temperature Control

The boiler has two separate circuits:

| Circuit | Operation | Phases | Typical power |
|---|---|---|---|
| Heat pump (compressor) | Normal house heating | **1-phase** (configurable, default L3) | 500–1,500 W |
| Heating element | Extra hot water | **2-phase** (the two remaining phases, default L1+L2) | 3,000–6,000 W |

The element phases are calculated automatically as the two phases *not* used by the compressor.

### Temperature Control for Extra Hot Water

A temperature sensor on the buffer tank is used to prevent unnecessary heating:

- **Max temp** (default 70°C): turns off extra hot water if the tank reaches this level
- **Min temp** (default 65°C): does not start extra hot water until the tank is below this level (even if everything else allows it)
- The temperature is shown in `sensor.sem_hot_water_temp`

### Minimum Runtime (anti-flicker)

To prevent the heating element from switching on/off every 30-second cycle, extra hot water is forced to stay ON for at least **5 minutes** (default, configurable) from the actual start time, even if the control logic wants to turn it off earlier.

---

## Legionella Disinfection

The boiler's built-in Legionella program runs automatically about once a week to heat the hot water to ≥ 65°C (configurable) and eliminate Legionella bacteria.

### How It Works

The system uses a **separate digital switch** to start the boiler's Legionella program:

- We turn the switch **ON** to start the program
- The boiler finishes the program and turns the switch **OFF** automatically when done
- If the switch is turned off prematurely, the cycle is aborted and the run is not counted as successful
- The run is confirmed via the temperature sensor – if the temperature does not reach the target value, the run is not registered as successful

### Start Priority Order

| Priority | Condition | Description |
|---|---|---|
| 1 | Solar surplus ≥ 3,000 W within the desired time window | Free solar power drives the program |
| 2 | Electricity price ≤ configured max price within the desired time window | Runs on cheap grid power |
| 3 | Interval exceeded by 50% (emergency run) | Runs regardless of price, avoids the night 23:00–06:00 |

### Settings

| Setting | Default | Description |
|---|---|---|
| Enabled | Yes | Turn the feature on/off |
| Legionella switch | – | The boiler's digital program switch |
| Confirmation temp | 65°C | Temperature that confirms a successful run |
| Interval | 7 days | How often disinfection should occur |
| Desired time window | 10–15 | Hours when solar is normally available |
| Max price | SEK 1.50/kWh | Do not run on grid power if more expensive |
| Run time | 60 min | Reference time (the boiler controls the actual time) |

---

## Pricing

| | Formula |
|---|---|
| **Buy price** | `(spot price + grid fees + energy tax) × (1 + VAT)` |
| **Sell price** | `spot price + extra revenue (electricity certificates, etc.)` |

---

## Entities

### Sensors

| Entity | Description |
|---|---|
| `sensor.sem_buy_price` | Current buy price SEK/kWh |
| `sensor.sem_sell_price` | Current sell price SEK/kWh |
| `sensor.sem_spot_price` | Nord Pool spot price |
| `sensor.sem_battery_charge_power` | Battery charging setpoint (W) |
| `sensor.sem_battery_discharge_power` | Battery discharging setpoint (W) |
| `sensor.sem_phase_l1_load` | Calculated phase load L1 (W) |
| `sensor.sem_phase_l2_load` | Calculated phase load L2 (W) |
| `sensor.sem_phase_l3_load` | Calculated phase load L3 (W) |
| `sensor.sem_house_load` | House load W – Meter 4 direct or calculated |
| `sensor.sem_solar_surplus` | Solar surplus (W) |
| `sensor.sem_hot_water_temp` | Buffer tank temperature (°C) |
| `sensor.sem_decision_reason` | Text explanation of the latest decision |
| `sensor.sem_operating_mode` | Active operating mode |
| `sensor.sem_legionella_active` | `on` when the Legionella program is running |
| `sensor.sem_legionella_days_since` | Days since the last confirmed run |
| `sensor.sem_legionella_next_due` | Date of the next planned run |
| `sensor.sem_legionella_temp_confirmed` | `on` if temp is confirmed during an ongoing run |
| `sensor.sem_negative_slots_ahead` | Number of quarter-hours with negative sell price in the coming 8h |
| `sensor.sem_best_discharge_price` | Best (highest) buy price for discharging in the coming 12h, with timestamp as attribute |
| `sensor.sem_best_charge_price` | Lowest buy price for charging in the coming 12h, with timestamp as attribute |
| `sensor.sem_yesterday_consumption` | Yesterday's consumption excl. EV charging (kWh) – requires a configured sensor |
| `sensor.sem_solar_next_2h_kwh` | Expected solar energy next 2 h (kWh, Solcast median) |
| `sensor.sem_solar_next_4h_kwh` | Expected solar energy next 4 h (kWh, Solcast median) |
| `sensor.sem_solar_next_8h_kwh` | Expected solar energy next 8 h (kWh, Solcast median) |
| `sensor.sem_peak_solar_kw_next_8h` | Expected peak solar power within 8 h (kW) – attribute: `peak_solar_time` |
| `sensor.sem_hours_to_solar_peak` | Hours until solar peak within 8 h |
| `sensor.sem_wait_for_solar` | `on` when the system is holding back grid charging in anticipation of solar |
| `sensor.sem_battery_accumulated_cost` | Accumulated cost (SEK) of energy currently in the battery – attributes: `solar_kwh_total`, `grid_kwh_total`, `average_price_sek_kwh`, `last_sell_price` |
| `sensor.sem_battery_average_price` | Average cost per kWh of energy currently in the battery (SEK/kWh) |

**Per charger** (replace `<charger>` with the charger's name in lowercase):

| Entity | Description |
|---|---|
| `sensor.sem_charger_<charger>_connected` | `on`/`off` – car physically connected |
| `sensor.sem_charger_<charger>_active_car` | Name of the selected car |
| `sensor.sem_charger_<charger>_current` | Charging current setpoint (A) |
| `sensor.sem_charger_<charger>_enabled` | Charging active `on`/`off` |

> `sensor.sem_phase_lX_load` is a **forecast**, not a measurement – it reflects the calculated phase load *after* the control decisions have been executed.

### Switches

| Entity | Function |
|---|---|
| `switch.sem_force_ev_charge_from_grid` | Force EV charging from the grid |
| `switch.sem_winter_mode` | Enable winter mode |
| `switch.sem_force_charge_battery_from_grid` | Force battery charging |

### Select

| Entity | Function |
|---|---|
| `select.sem_operating_mode` | Select operating mode: `auto` / `winter` / `force_charge_ev` / `force_charge_battery` / `manual` |
| `select.sem_charger_<charger>_active_car` | Select which car is connected to the charger |

### Number (adjustable in real time)

| Entity | Function |
|---|---|
| `number.sem_battery_min_soc` | Battery min SOC % |
| `number.sem_battery_max_soc` | Battery max SOC % |
| `number.sem_ev_soc_target` | Global default charging target % |
| `number.sem_winter_cheap_threshold` | Cheap price threshold (SEK/kWh) |
| `number.sem_winter_expensive_threshold` | Expensive price threshold (SEK/kWh) |
| `number.sem_winter_min_soc` | Winter min SOC % |
| `number.sem_winter_max_soc` | Winter max SOC % |

---

## Installation via HACS

1. Go to HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/fjonson95/smart_energy_manager`, category: Integration
3. Install "Smart Energy Manager"
4. Restart Home Assistant
5. Settings → Integrations → Add → Smart Energy Manager

---

## Configuration

### Dependencies
These HACS integrations must be installed and configured:
- **nordpool** – electricity price sensor
- **solcast_solar** – solar forecast (optional but recommended)

### Configuration Flow

Configuration takes place in six steps:

**Step 1 – Grid & Pricing**
- Nord Pool sensor (required)
- Grid meter per phase (L1/L2/L3)
- Current sensors per phase (for phase protection)
- Max current per phase (default 20 A)
- Grid voltage (default 230 V)
- Grid fees, energy tax, VAT, sales compensation
- House load controller – point to Meter 4 for direct house load measurement (recommended)
- Grid meter unit – select `kW` if Meter 1 reports in kilowatts
- EV charger power unit – select `kW` if the charger reports in kilowatts
- Yesterday's consumption – optional sensor for `sensor.sem_yesterday_consumption`

**Step 2 – Solar Panels**
- Solar inverter total and per phase
- Solcast forecasts today/tomorrow

**Step 3 – Battery**
- SOC sensor, power sensor, charging and discharging entities
- Capacity (kWh) and max power (kW)
- Min/max SOC limits

**Step 4 – Electric Boiler / Heat Pump**
- Power sensor (Meter 5) – the compressor's on/off status is read from the power, no separate switch needed
- Switch for extra hot water (heating element)
- Compressor phase (1-phase, default L3) – element phases are calculated automatically
- Heating element rated power (kW)
- **Buffer tank temperature sensor** (optional)
- **Max temp for extra hot water** (default 70°C)
- **Min temp for extra hot water** (default 65°C)
- **Minimum runtime for extra hot water** (default 5 min) – prevents on/off flicker

**Step 5 – Legionella Disinfection**
- Enable/disable the feature
- **Legionella switch** (the boiler's digital program switch)
- **Confirmation temp** (default 65°C – the run is approved when the tank reaches this temp)
- Interval in days (default 7)
- Desired time window (default 10–15)
- Max electricity price for running on grid power (default SEK 1.50/kWh)
- Run time in minutes (reference time)

**Step 6 – EV Charger**
- Add one or more chargers
- Per charger: name, connection sensor, switch, current setpoint entity, charger phase count (1-phase or 3-phase hardware)
- Per car on the charger: name, SOC sensor, SOC target, **the car's phase count** (1/2/3-phase built-in charger – governs phase load), phase (for 1- and 2-phase cars)
- Repeat for each charger

All settings can be edited afterwards via **Settings → Integrations → Smart Energy Manager → Configure**.

### Unit Notes

| Sensor | Unit in HA | Setting |
|---|---|---|
| Meter 1 (grid meter) | kW | Grid meter unit → **kW** |
| Meter 3 / SolInv_prod | W | (default W) |
| BatInv_in_out | W, pos=charging, neg=discharging | (default W) |
| Meter 4 (house load) | W | House load controller → Meter 4 |
| Meter 5 (boiler) | W | Boiler power sensor → Meter 5 |
| Car_charging (internal) | kW | EV charger power unit → **kW** |

---

## Logging

```yaml
logger:
  logs:
    custom_components.smart_energy_manager: debug
```

---

## Example: Automation for Winter Mode

```yaml
automation:
  - alias: "Winter mode October–March"
    trigger:
      - platform: time
        at: "00:01:00"
    condition:
      - condition: template
        value_template: "{{ now().month in [10,11,12,1,2,3] }}"
    action:
      - service: select.select_option
        target:
          entity_id: select.sem_operating_mode
        data:
          option: winter
```

## Example: Dashboard (Lovelace)

```yaml
type: vertical-stack
cards:
  - type: entity
    entity: select.sem_operating_mode
    name: Operating mode

  - type: glance
    entities:
      - entity: sensor.sem_buy_price
        name: Buy SEK/kWh
      - entity: sensor.sem_sell_price
        name: Sell SEK/kWh
      - entity: sensor.sem_solar_surplus
        name: Solar surplus W
      - entity: sensor.sem_house_load
        name: House load W
      - entity: sensor.sem_hot_water_temp
        name: Hot water °C

  - type: gauge
    entity: sensor.sem_battery_charge_power
    name: Battery charging W
    max: 5000

  - type: entities
    title: Charger A
    entities:
      - entity: sensor.sem_charger_laddare_a_connected
        name: Connected
      - entity: select.sem_charger_laddare_a_active_car
        name: Selected car
      - entity: sensor.sem_charger_laddare_a_current
        name: Charging current A
      - entity: sensor.sem_charger_laddare_a_enabled
        name: Charging active

  - type: entities
    title: Legionella
    entities:
      - entity: sensor.sem_legionella_active
        name: In progress
      - entity: sensor.sem_legionella_temp_confirmed
        name: Temp confirmed
      - entity: sensor.sem_legionella_days_since
        name: Days since last
      - entity: sensor.sem_legionella_next_due
        name: Next run

  - type: entity
    entity: sensor.sem_decision_reason
    name: Latest decision
```
