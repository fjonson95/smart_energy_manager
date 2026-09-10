#!/usr/bin/env python3
"""
SEM Backtest – replays historical sensor data through the FULL live decision
pipeline: EnergyPlanner.build_plan() -> EnergyController.compute() ->
EnergyController.apply_plan_executor() (the exact same method coordinator.py
calls in production, so this reflects real behavior, not just the standalone
EnergyController.compute() heuristics).

Every slot runs the pipeline TWICE:

  - "Shadow" columns (unprefixed / actual_*): battery_soc_pct comes from the
    REAL historical SOC in the data. Answers "what would SEM have decided at
    this historical moment" — a sanity check of the decision logic, not a
    cost estimate, since a real operator (or the old control system) may
    have already deviated from what SEM would have chosen.
  - "Simulated" columns (sim_*, P7-2): battery_soc_pct instead comes from a
    running SOC that this script itself integrates forward from SEM's own
    prior decisions (a simplified battery model — see _simulate_battery_soc).
    Solar production and house load are NOT simulated (no weather/load model
    exists) — they stay the real historical readings, matching P7-2's scope
    of "a simplified battery model following the decisions", not a full
    house simulation. Grid import/export/cost for this column are DERIVED
    from the energy balance (house_load + battery_charge - battery_discharge
    - solar), not measured, since a diverging SOC trajectory means the real
    historical grid reading no longer applies.
  - "Reference" columns (ref_*, P7-2): the same energy balance with the
    battery held out entirely (grid = house_load - solar) — "what the house
    would have cost with solar but no battery and no control", the baseline
    the plan's acceptance criterion measures savings against.

Solar per price-slot is a p50 proxy built from the ACTUAL historical solar
reading at that time (no historical Solcast p10/p50 archive exists), with a
fixed haircut for p10 (SETTINGS["p10_haircut"]). Good enough to exercise the
floor/export logic, not a stand-in for the real forecast.

Two input formats, auto-detected from whether `input` is a file or a directory:

  - A CSV file: the old HA-logbook-export format, hourly resolution, one row
    per {timestamp, entity_id, state} (see load_csv()). The three bundled
    samples that used to live in testdata/timdata/ were removed — each
    covered months on paper but was really ~50 hourly rows glued to a single
    multi-week/multi-month gap, which the forward-simulation and
    model-fidelity replay integrate straight through as if it were one real
    hour. Confirmed empirically: SOC errors up to 63 percentage points and
    import errors over +1800% on the old files, against -18%/+2pp on the
    genuinely continuous testdata/history/ data below. The loader still
    works for a real single-file sample — it just needs to actually be
    hourly-continuous (or close to it) for the P7-2 columns to mean anything.
  - A directory (testdata/history/, from P7-1): one CSV per quantity
    (solar/house-load/outdoor-temp/battery-SOC at hourly resolution, price at
    real quarter-hour resolution — matching production, where price slots are
    genuinely 15 minutes, not an assumed hour). The hourly quantities are
    forward-filled onto the price series' quarter-hour grid. Grid-phase
    (grid_l1/l2/l3_hourly.csv) and battery-inout (battery_inout_hourly.csv)
    history are optional additions (see _HISTORY_DIR_POWER_FILES) — present,
    they make the "shadow"/actual_* import/export/cost accounting AND the
    P7-2 model-fidelity replay meaningful for this source too; absent,
    has_actual_power_data falls back to False and only the decision/
    plan_action columns and the sim_*/ref_* P7-2 columns (always derived
    from the energy balance, never measured) are meaningful. See
    testdata/history/Series info.txt for exactly what period and quantities
    are available.

Usage (from repo root):
    python testdata/backtest.py path/to/hourly_sample.csv [--out results.csv]
    python testdata/backtest.py testdata/history [--out results.csv]

Options:
    --out FILE      Write timeline CSV to FILE (default: backtest_result.csv)
    --min-soc N     Override battery_min_soc (default: from SETTINGS)
    --percentile N  Override export_sell_percentile (default: from SETTINGS)
    --date-from     YYYY-MM-DD  Filter start date (inclusive)
    --date-to       YYYY-MM-DD  Filter end date (inclusive)
"""
from __future__ import annotations

import sys
import os
import csv
import bisect
import argparse
import dataclasses
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Add repo root to path so we can import SEM modules ────────────────────────
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, REPO_ROOT)

from custom_components.smart_energy_manager.energy_controller import (
    EnergyController, EnergyState, ChargerState, ChargerConfig, CarConfig,
)
from custom_components.smart_energy_manager.energy_planner import EnergyPlanner
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule, PriceSlot,
)
from custom_components.smart_energy_manager.const import (
    NO_CAR_SELECTED, DEFAULT_HEAT_BALANCE_TEMP, DEFAULT_HEAT_FACTOR_KWH_DD, DEFAULT_BASE_DHW_KWH,
)

# ── Konfiguration – matchar den faktiska (P1-4-korrigerade) HA-konfigurationen,
# inte de gamla felaktiga värdena (33 kWh, 0.07, 20A). Se README "What's New"
# för P1-4/P2-2/P2-3/P3-2 för varifrån dessa kommer.
SETTINGS = {
    "grid_fees_sek_kwh":        0.45,
    "energy_tax_sek_kwh":       0.536,
    "vat_rate":                 0.25,
    "sell_extra_revenue":       0.065,
    "battery_capacity_kwh":     30.69,
    "battery_max_power_kw":     8.0,
    "battery_min_soc":          20.0,
    "battery_max_soc":          99.0,
    "export_sell_percentile":   0.80,
    "export_min_solar_tomorrow_kwh": 5.0,
    "export_min_sell_price_sek_kwh": 0.70,
    "eta_roundtrip":            0.87,
    "cycle_cost_sek_kwh":       0.05,
    "grid_scale":               1000.0,   # grid_power_unit = kW
    "battery_power_inverted":   True,
    "max_current_per_phase":    17,
    "max_export_w":             14000.0,
    "grid_voltage":             230,
    # Grov p10-approximation: historiska per-slot p10-prognoser arkiverades
    # aldrig, så vi använder faktisk produktion som p50-proxy och en fast
    # nedskrivning för p10. Se build_price_schedule().
    "p10_haircut":              0.70,
}

# Sensor entity_id → EnergyState field + transform
SENSOR_MAP = {
    "sensor.nordpool_kwh_se3_sek_3_10_0_2":                    ("nordpool_raw",           lambda v: float(v) / 100.0),
    "sensor.el_forbruk_power_power":                            ("house_load_w",           float),
    "sensor.sg_total_active_power":                             ("solar_w",                float),
    "sensor.sonnenbatterie_271100_state_battery_percentage_real":("battery_soc_pct",       float),
    "sensor.sonnenbatterie_271100_state_battery_inout":         ("battery_power_w_raw",    float),
    "sensor.elmatare_active_power_l1":                          ("grid_l1_kw",             float),
    "sensor.elmatare_active_power_l2":                          ("grid_l2_kw",             float),
    "sensor.elmatare_active_power_l3":                          ("grid_l3_kw",             float),
    "sensor.elmatare_current_l1":                               ("grid_cur_l1",            float),
    "sensor.elmatare_current_l2":                               ("grid_cur_l2",            float),
    "sensor.elmatare_current_l3":                               ("grid_cur_l3",            float),
    "sensor.solcast_pv_forecast_forecast_remaining_today":      ("solcast_today",          float),
    "sensor.solcast_pv_forecast_forecast_tomorrow":             ("solcast_tomorrow",       float),
    "sensor.boiler_outdoortemp":                                ("outdoor_temp",           float),
    "sensor.boiler_dhw_curtemp":                                ("hot_water_temp",         float),
    "sensor.ceed_ev_battery_level":                             ("ev_soc",                 float),
    "switch.boiler_dhw_disinfecting":                           ("disinfecting",           lambda v: str(v).lower() in ("on", "true", "1")),
    "switch.thermostat_dhw_chargethermostat_dhw_charge":        ("extra_hot_water_on",     lambda v: str(v).lower() in ("on", "true", "1")),
    "sensor.0xf4ce365d8573ed2f_ev_status":                      ("ev_status",              str),
    "switch.0xf4ce365d8573ed2f":                                ("charger_switch",         lambda v: str(v).lower() in ("on", "true", "1")),
    "sensor.0xf4ce365d8573ed2f_total_active_power":             ("charger_power_kw",        float),
    "number.0xf4ce365d8573ed2f_charge_limit":                   ("charger_current_a",       float),
    "sensor.ivt_total_active_power":                            ("heat_pump_power_w",       float),
    "sensor.el_forbruk_power_energy_daily":                     ("yesterday_consumption_kwh", float),
    "sensor.sungrow_sg12rt_phase_a_current":                    ("solar_l1_a",              float),
    "sensor.sungrow_sg12rt_phase_b_current":                    ("solar_l2_a",              float),
    "sensor.sungrow_sg12rt_phase_c_current":                    ("solar_l3_a",              float),
}


def _safe(v: str, transform):
    try:
        return transform(v)
    except (ValueError, TypeError):
        return None


def load_csv(path: str) -> dict[datetime, dict[str, str]]:
    """Load CSV into {timestamp: {entity_id: state}}. Uses last known value (forward-fill)."""
    rows: list[tuple[datetime, str, str]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = row["last_changed"].rstrip("Z")
            try:
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            rows.append((ts, row["entity_id"], row["state"]))

    rows.sort(key=lambda r: r[0])

    # Group by hour (truncate to hour boundary)
    pivot: dict[datetime, dict[str, str]] = defaultdict(dict)
    for ts, eid, state in rows:
        hour_ts = ts.replace(minute=0, second=0, microsecond=0)
        pivot[hour_ts][eid] = state

    # Forward-fill: carry last known value to subsequent hours
    filled: dict[datetime, dict[str, str]] = {}
    last_known: dict[str, str] = {}
    for ts in sorted(pivot.keys()):
        last_known.update(pivot[ts])
        filled[ts] = dict(last_known)

    return filled


# P7-1: the multi-file CSV layout produced in testdata/history/ (one CSV per
# quantity, hourly statistics for load/solar/temp/SOC, quarter-hour raw
# history for price). Maps back onto the same SENSOR_MAP entity_ids so
# build_state() doesn't need to know which loader produced the pivot.
_HISTORY_DIR_SERIES = {
    "solar_hourly.csv":        "sensor.sg_total_active_power",
    "house_load_hourly.csv":   "sensor.el_forbruk_power_power",
    "outdoor_temp_hourly.csv": "sensor.boiler_outdoortemp",
    "battery_soc_hourly.csv":  "sensor.sonnenbatterie_271100_state_battery_percentage_real",
    # Grid-fas- och batteri-inout-historik (utökning av P7-1): valfria — om
    # filerna saknas faller grid_power_l1/l2/l3 och battery_power_w tillbaka
    # till EnergyState-defaulten 0, precis som innan denna utökning.
    "grid_l1_hourly.csv":      "sensor.elmatare_active_power_l1",
    "grid_l2_hourly.csv":      "sensor.elmatare_active_power_l2",
    "grid_l3_hourly.csv":      "sensor.elmatare_active_power_l3",
    "battery_inout_hourly.csv":"sensor.sonnenbatterie_271100_state_battery_inout",
    # Värmepumpens (IVT) egen effekt - saknades tidigare, heat_pump_power_w
    # defaultade till 0.0 i varje backtest-körning. Timvis, sep 2025-aug 2026.
    "heat_pump_power_hourly.csv":"sensor.ivt_total_active_power",
}

# Filer vars närvaro avgör om "actual"/skugg-redovisningen och P7-2:s
# modelltrohets-återspelning har något att jämföra mot för denna källa.
_HISTORY_DIR_POWER_FILES = (
    "grid_l1_hourly.csv", "grid_l2_hourly.csv", "grid_l3_hourly.csv",
    "battery_inout_hourly.csv",
)


def _read_hourly_series(path: str) -> tuple[list[datetime], list[float]]:
    ts_list: list[datetime] = []
    val_list: list[float] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["timestamp_utc"])
                v = float(row["mean"])
            except (KeyError, ValueError):
                continue
            ts_list.append(ts)
            val_list.append(v)
    pairs = sorted(zip(ts_list, val_list))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _forward_fill_at(sorted_ts: list[datetime], sorted_vals: list[float], target_ts: datetime) -> Optional[float]:
    """Value in effect at target_ts: the latest sample at or before it."""
    idx = bisect.bisect_right(sorted_ts, target_ts) - 1
    if idx < 0:
        return None
    return sorted_vals[idx]


def load_history_dir(history_dir: str) -> dict[datetime, dict[str, str]]:
    """Load the P7-1 testdata/history/ layout into the same
    {timestamp: {entity_id: state}} pivot shape load_csv() produces, at
    quarter-hour resolution (the price series' native granularity). The
    hourly quantities are forward-filled onto that finer grid – they don't
    change fast enough for that approximation to matter.
    """
    series: dict[str, tuple[list[datetime], list[float]]] = {}
    for fname, entity_id in _HISTORY_DIR_SERIES.items():
        path = os.path.join(history_dir, fname)
        if os.path.exists(path):
            series[entity_id] = _read_hourly_series(path)

    price_path = os.path.join(history_dir, "price_quarterhour.csv")
    price_ts: list[datetime] = []
    price_v: list[float] = []
    with open(price_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc)
                v = float(row["spot_sek_kwh"])
            except (KeyError, ValueError):
                continue
            price_ts.append(ts)
            price_v.append(v)
    price_pairs = sorted(zip(price_ts, price_v))

    pivot: dict[datetime, dict[str, str]] = {}
    for ts, spot_sek in price_pairs:
        # SENSOR_MAP divides by 100 (öre -> SEK) for this entity, so store
        # the raw öre-equivalent to keep build_state()'s transform pipeline
        # identical regardless of which loader produced the pivot.
        row: dict[str, str] = {"sensor.nordpool_kwh_se3_sek_3_10_0_2": str(spot_sek * 100.0)}
        for entity_id, (sts, svals) in series.items():
            v = _forward_fill_at(sts, svals, ts)
            if v is not None:
                row[entity_id] = str(v)
        pivot[ts] = row

    return pivot


def build_price_schedule(
    price_slots: list[tuple[datetime, float]],
    solar_w_by_slot: dict[datetime, float],
    ref_ts: datetime,
    s: dict,
    slot_minutes: int = 60,
) -> Optional[PriceSchedule]:
    """Build a minimal PriceSchedule from Nordpool prices at the caller's
    native resolution (60 min for the old single-file testdata/timdata/*.csv
    format, 15 min for the real quarter-hour history in testdata/history/ –
    matching production, where price slots are genuinely quarter-hours, not
    an assumed half-hour or hour).

    Solar per slot is approximated from the ACTUAL historical production at
    that time (we never archived historical Solcast p10/p50 per slot). This
    is the p50 proxy; p10 is that same value with a fixed haircut applied
    (SETTINGS["p10_haircut"]) since no real pessimistic history exists.
    Good enough to exercise the floor/export logic, not a substitute for the
    real forecast the live system uses.
    """
    gf  = s["grid_fees_sek_kwh"]
    et  = s["energy_tax_sek_kwh"]
    vat = s["vat_rate"]
    ex  = s["sell_extra_revenue"]
    haircut = s["p10_haircut"]
    slot_h = slot_minutes / 60.0

    slots: list[PriceSlot] = []
    for ts, spot in price_slots:
        buy  = (spot + gf + et) * (1 + vat)
        sell = spot + ex
        slot_start = ts
        slot_end   = ts + timedelta(minutes=slot_minutes)
        solar_kw = max(0.0, solar_w_by_slot.get(ts, 0.0)) / 1000.0
        slots.append(PriceSlot(
            start=slot_start, end=slot_end,
            spot_sek=spot, buy_sek=buy, sell_sek=sell,
            solar_kw=solar_kw, solar_kwh=solar_kw * slot_h,
            solar_kwh_p10=solar_kw * slot_h * haircut,
        ))

    if not slots:
        return None

    future_slots = [sl for sl in slots if sl.end > ref_ts]
    if not future_slots:
        return None

    ps = PriceSchedule()
    ps.slots = future_slots
    ps.should_wait_for_solar = False
    return ps


def build_state(row: dict[str, str], price_schedule: Optional[PriceSchedule], s: dict, now: datetime) -> EnergyState:
    vals: dict[str, any] = {}
    for eid, (field, tf) in SENSOR_MAP.items():
        raw = row.get(eid)
        if raw is not None:
            v = _safe(raw, tf)
            if v is not None:
                vals[field] = v

    spot     = vals.get("nordpool_raw", 0.0)
    gf       = s["grid_fees_sek_kwh"]
    et       = s["energy_tax_sek_kwh"]
    vat      = s["vat_rate"]
    ex       = s["sell_extra_revenue"]
    buy_p    = (spot + gf + et) * (1 + vat)
    sell_p   = spot + ex
    grid_sc  = s["grid_scale"]

    bat_raw  = vals.get("battery_power_w_raw", 0.0)
    bat_w    = -bat_raw if s["battery_power_inverted"] else bat_raw

    ev_connected = str(vals.get("ev_status", "")).lower() in (
        "connected", "charging", "plugged_in", "pluggedin", "waiting", "ready"
    )
    ev_soc_val = vals.get("ev_soc")
    car = CarConfig(
        name="Kia",
        ev_soc=str(ev_soc_val) if ev_soc_val is not None else None,
        ev_soc_target=95.0,
        car_phases=1,
        phase="L1",
    )
    cfg = ChargerConfig(
        name="Amina",
        charger_switch="switch.0xf4ce365d8573ed2f",
        charger_current="number.0xf4ce365d8573ed2f_charge_limit",
        connected_sensor="sensor.0xf4ce365d8573ed2f_ev_status",
        charger_power="sensor.0xf4ce365d8573ed2f_total_active_power",
        phases=3,
        phase="L1",
        cars=[car],
    )
    ev_power_kw = vals.get("charger_power_kw", 0.0)
    ev_current_a = vals.get("charger_current_a", 0.0)
    charger = ChargerState(
        config=cfg,
        connected=ev_connected,
        active_car_name="Kia" if ev_connected else NO_CAR_SELECTED,
        current_a=ev_current_a,
        power_w=ev_power_kw * s["grid_scale"],
        soc_pct=ev_soc_val,
    )

    return EnergyState(
        solar_power_w             = vals.get("solar_w", 0.0),
        solar_forecast_today_kwh  = vals.get("solcast_today", 0.0),
        solar_forecast_tomorrow_kwh = vals.get("solcast_tomorrow", 0.0),
        battery_soc_pct           = vals.get("battery_soc_pct", 50.0),
        battery_power_w           = bat_w,
        battery_capacity_kwh      = s["battery_capacity_kwh"],
        battery_max_power_kw      = s["battery_max_power_kw"],
        chargers                  = [charger],
        house_load_w              = vals.get("house_load_w", 0.0),
        heat_pump_power_w         = vals.get("heat_pump_power_w", 0.0),
        heat_pump_phase           = "L2",
        heat_pump_patron_power_kw = 6.0,
        hot_water_temp_c          = vals.get("hot_water_temp"),
        extra_hot_water_on        = vals.get("extra_hot_water_on", False),
        extra_hot_water_max_temp  = 70.0,
        extra_hot_water_min_temp  = 65.0,
        grid_power_l1             = vals.get("grid_l1_kw", 0.0) * grid_sc,
        grid_power_l2             = vals.get("grid_l2_kw", 0.0) * grid_sc,
        grid_power_l3             = vals.get("grid_l3_kw", 0.0) * grid_sc,
        grid_current_l1           = vals.get("grid_cur_l1", 0.0),
        grid_current_l2           = vals.get("grid_cur_l2", 0.0),
        grid_current_l3           = vals.get("grid_cur_l3", 0.0),
        spot_price_sek_kwh        = spot,
        buy_price_sek_kwh         = buy_p,
        sell_price_sek_kwh        = sell_p,
        price_schedule            = price_schedule,
        outdoor_temp_c            = vals.get("outdoor_temp"),
        yesterday_consumption_kwh = vals.get("yesterday_consumption_kwh"),
        disinfecting_active       = vals.get("disinfecting", False),
        battery_avg_cost_sek_kwh  = 0.0,
        now                       = now,
    )


def _simulate_battery_soc(
    soc_pct: float,
    charge_w: float,
    discharge_w: float,
    slot_h: float,
    capacity_kwh: float,
    eta_roundtrip: float,
) -> float:
    """P7-2 batterimodell: integrera SOC framåt utifrån SEM:s eget beslut i
    denna slot, i stället för att läsa nästa slots SOC ur historiken.

    eta_roundtrip är en tur-och-retur-verkningsgrad (ellagring ut / el in för
    en full cykel). Utan uppmätt fördelning mellan laddnings- och
    urladdningsförlust delas den lika mellan benen (sqrt) — en förenkling,
    men den enda som inte kräver data vi inte har.
    """
    eta_leg = eta_roundtrip ** 0.5
    energy_kwh = soc_pct / 100.0 * capacity_kwh
    if charge_w > 0:
        energy_kwh += charge_w / 1000.0 * slot_h * eta_leg
    elif discharge_w > 0:
        energy_kwh -= discharge_w / 1000.0 * slot_h / eta_leg
    energy_kwh = max(0.0, min(capacity_kwh, energy_kwh))
    return max(0.0, min(100.0, energy_kwh / capacity_kwh * 100.0))


def run_backtest(
    data_path: str,
    out_path: str,
    overrides: dict,
    date_from: Optional[datetime] = None,
    date_to:   Optional[datetime] = None,
    drought_oracle: bool = False,
):
    s = {**SETTINGS, **overrides}

    controller = EnergyController(
        battery_min_soc             = s["battery_min_soc"],
        battery_max_soc             = s["battery_max_soc"],
        max_current_per_phase       = s["max_current_per_phase"],
        grid_voltage                = s["grid_voltage"],
        max_export_w                = s["max_export_w"],
    )
    planner = EnergyPlanner(
        battery_min_soc                = s["battery_min_soc"],
        battery_max_soc                = s["battery_max_soc"],
        export_sell_percentile         = s["export_sell_percentile"],
        export_min_sell_price_sek_kwh  = s["export_min_sell_price_sek_kwh"],
        export_min_solar_tomorrow_kwh  = s["export_min_solar_tomorrow_kwh"],
        sell_solar_min_price           = 0.80,
        eta_roundtrip                  = s["eta_roundtrip"],
        cycle_cost_sek_kwh             = s["cycle_cost_sek_kwh"],
    )

    sys.stdout.buffer.write(f"Laddar {data_path} ...\n".encode("utf-8"))
    is_history_dir = os.path.isdir(data_path)
    if is_history_dir:
        pivot = load_history_dir(data_path)
        slot_minutes = 15
        # Grid-fas-/batteri-inout-historik är en valfri utökning av P7-1
        # (se _HISTORY_DIR_POWER_FILES) – utan den stannar grid_power_l1/l2/l3
        # och battery_power_w på EnergyState-defaulten 0, och "actual"/
        # skugg-redovisningen samt P7-2:s modelltrohets-återspelning blir
        # meningslösa. Med den är de lika meningsfulla som för CSV-formatet.
        has_actual_power_data = all(
            os.path.exists(os.path.join(data_path, fname))
            for fname in _HISTORY_DIR_POWER_FILES
        )
    else:
        pivot = load_csv(data_path)
        slot_minutes = 60
        has_actual_power_data = True

    timestamps = sorted(pivot.keys())
    if date_from:
        timestamps = [t for t in timestamps if t >= date_from]
    if date_to:
        end = date_to + timedelta(days=1)
        timestamps = [t for t in timestamps if t < end]

    if not timestamps:
        sys.stdout.buffer.write(b"Inga tidsstamplar matchar filtret.\n")
        return

    sys.stdout.buffer.write(f"{len(timestamps)} timmar ({timestamps[0]} - {timestamps[-1]})\n".encode("utf-8"))

    # Samla alla timpriser och faktisk solproduktion (p50-proxy) för prisschema
    all_prices: list[tuple[datetime, float]] = []
    hourly_solar_w: dict[datetime, float] = {}
    for ts, row in pivot.items():
        raw = row.get("sensor.nordpool_kwh_se3_sek_3_10_0_2")
        if raw:
            v = _safe(raw, lambda x: float(x) / 100.0)
            if v is not None:
                all_prices.append((ts, v))
        solar_raw = row.get("sensor.sg_total_active_power")
        if solar_raw:
            sv = _safe(solar_raw, float)
            if sv is not None:
                hourly_solar_w[ts] = sv
    all_prices.sort()

    # Steg 1A-verifiering (extern granskning 2026-09-10): build_plan() tar
    # emot load_shape_p50/p75 (form×nivå, se coordinator._get_load_shape())
    # i produktion, men fick dem ALDRIG här förut — alla tidigare Steg 0-3-
    # backtest-siffror kördes mot den platta reservmodellen, inte den
    # faktiska lastprofilen. Byggd med samma kausalitet som coordinator:
    # bara FÖREGÅENDE, färdiga dygn (aldrig dagens eget ännu ofullständiga
    # dygn eller framtida dygn) ingår i percentil-fönstret vid tidpunkt ts.
    hourly_house_load_w: dict[datetime, float] = {}
    for ts, row in pivot.items():
        load_raw = row.get("sensor.el_forbruk_power_power")
        if load_raw:
            lv = _safe(load_raw, float)
            if lv is not None:
                hourly_house_load_w[ts] = lv

    _day_hour_totals: dict = {}
    for t, w in hourly_house_load_w.items():
        d = t.astimezone(timezone.utc).date()
        h = t.astimezone(timezone.utc).hour
        _day_hour_totals.setdefault(d, {})[h] = w
    _daily_load_fractions: dict = {}
    for d, hours in _day_hour_totals.items():
        day_total = sum(hours.values())
        if day_total > 0:
            _daily_load_fractions[d] = [
                (hours[h] / day_total) if h in hours else None
                for h in range(24)
            ]
    _sorted_load_days = sorted(_daily_load_fractions.keys())

    # load_shape_p50/p75 (form) multipliceras i build_plan() mot
    # predicted_daily_kwh (nivån, gradtimmodellen) – INTE mot _eff_daily_kwh
    # (energy_planner.py:_load_kw_at). backtest.py hårdkodade tidigare
    # predicted_daily_kwh=0.0 (ingen temperaturmodell fanns), vilket gjort
    # load_shape helt overksam om den bara kopplats in ensam (form × 0 = 0,
    # sämre än den platta fallbacken). Byggd här med SAMMA formel/konstanter
    # som coordinator.py (rad 1051-1059): base + k×max(0, t_bal-temp), med
    # gårdagens dygnsmedeltemp om den finns, annars slotens egen temp.
    _daily_temp_avg: dict = {}
    _day_temp_samples: dict = {}
    for t, row in pivot.items():
        temp_raw = row.get("sensor.boiler_outdoortemp")
        if temp_raw:
            tv = _safe(temp_raw, float)
            if tv is not None:
                _day_temp_samples.setdefault(t.astimezone(timezone.utc).date(), []).append(tv)
    for d, samples in _day_temp_samples.items():
        _daily_temp_avg[d] = sum(samples) / len(samples)

    def _predicted_daily_kwh_at(ts: datetime, current_temp_c: Optional[float]) -> float:
        day0 = ts.astimezone(timezone.utc).date()
        yesterday_avg = _daily_temp_avg.get(day0 - timedelta(days=1))
        temp_for_model = yesterday_avg if yesterday_avg is not None else current_temp_c
        if temp_for_model is None:
            return 0.0
        return DEFAULT_BASE_DHW_KWH + DEFAULT_HEAT_FACTOR_KWH_DD * max(0.0, DEFAULT_HEAT_BALANCE_TEMP - temp_for_model)

    def _load_shape_at(ts: datetime, percentile: float) -> Optional[list]:
        day0 = ts.astimezone(timezone.utc).date()
        window_days = [d for d in _sorted_load_days if d < day0][-21:]
        if not window_days:
            return None
        result: list = []
        for hour in range(24):
            vals = sorted(
                _daily_load_fractions[d][hour] for d in window_days
                if _daily_load_fractions[d][hour] is not None
            )
            if not vals:
                result.append(None)
                continue
            idx = min(int(percentile * len(vals)), len(vals) - 1)
            result.append(vals[idx])
        if all(v is None for v in result):
            return None
        return result

    # --drought-oracle (testverktyg för v2 av torkrisk-påslaget, docs/
    # v1_implementation_plan.md "Torkrisk-påslag från SMHI:s väderprognos"):
    # bygger weather_forecast från riktig HISTORISK väderdata (Open-Meteo
    # archive-api, se testdata/history_jan_apr2026/_build_weather_oracle.py)
    # istället för en arkiverad SMHI-prognos (finns inte). Ett orakel för en
    # perfekt väderprognos — se plandokumentets brasklapp om verifiering.
    _weather_oracle_path = os.path.join(data_path, "weather_oracle.csv") if is_history_dir else None
    daily_weather: dict = {}
    if drought_oracle and _weather_oracle_path and os.path.exists(_weather_oracle_path):
        with open(_weather_oracle_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                daily_weather[datetime.fromisoformat(row["date"]).date()] = (
                    row["condition"], float(row["temp_max_c"]), float(row["temp_min_c"]),
                )

    def _oracle_weather_forecast(ts: datetime) -> list[tuple]:
        day0 = ts.astimezone(timezone.utc).date()
        out = []
        for offset in range(1, 7):
            d = day0 + timedelta(days=offset)
            if d in daily_weather:
                condition, tmax, tmin = daily_weather[d]
                out.append((d, condition, tmax, tmin))
        return out

    # Ackumulatorer för sammanfattning
    total_grid_import_kwh  = 0.0
    total_grid_export_kwh  = 0.0
    total_grid_cost_sek    = 0.0
    total_export_rev_sek   = 0.0
    total_solar_kwh        = 0.0
    total_bat_charge_kwh   = 0.0
    total_bat_discharge_kwh = 0.0
    n_export_hours         = 0
    n_charge_hours         = 0
    n_discharge_hours      = 0

    # P7-2: framåtsimulerad batterimodell + referens utan batteri/styrning
    sim_soc: Optional[float] = None
    sim_prev_battery_power_w: Optional[float] = None
    # Speglar BatteryAccumulatedCostSensor (sensor.py) i miniatyr: sol till
    # batteri bokförs till säljpris (alternativkostnad), nät till köppris,
    # urladdning skriver INTE ner snittkostnaden (bara den totala poolen
    # krymper proportionellt - se CLAUDE.md "Batterikostnad"). Utan den här
    # spegling var battery_avg_cost_sek_kwh alltid 0.0 i backtest (state.py
    # hårdkodar den), vilket gjorde v1.0 steg 3:s regel 4-broms mot att
    # sälja under lagringskostnad omöjlig att verifiera.
    sim_avg_cost_sek_kwh: float = 0.0
    total_sim_import_kwh   = 0.0
    total_sim_export_kwh   = 0.0
    total_sim_cost_sek     = 0.0
    total_sim_rev_sek      = 0.0
    total_sim_charge_kwh   = 0.0
    total_sim_discharge_kwh = 0.0
    total_ref_import_kwh   = 0.0
    total_ref_export_kwh   = 0.0
    total_ref_cost_sek     = 0.0
    total_ref_rev_sek      = 0.0

    # P7-2 modellvalidering: spela upp historikens EGNA batteribeslut (inte
    # SEM:s) genom samma modell. sim_* ovan använder SEM:s nya beslut och
    # avviker därför från historiken AV DESIGN (det är hela poängen med att
    # jämföra policyer) – den jämförelsen kan aldrig validera fysiken. Genom
    # att i stället återspela vad som faktiskt hände kan man se om
    # batterimodellen och energibalansformeln (grid = last + laddning -
    # urladdning - sol) själva är rimliga, oberoende av vilken policy som
    # körs. Endast meningsfullt när has_actual_power_data är sant.
    replay_soc: Optional[float] = None
    total_replay_import_kwh = 0.0
    total_replay_export_kwh = 0.0

    fieldnames = [
        "timestamp", "spot_sek_kwh", "buy_sek_kwh", "sell_sek_kwh",
        "solar_w", "house_load_w", "battery_soc_pct", "battery_power_w",
        "grid_kw", "plan_action", "export_floor_kwh",
        "decision_bat_charge_w", "decision_bat_discharge_w",
        "decision_extra_hot_water", "reason",
        "actual_grid_import_kwh", "actual_grid_export_kwh",
        "actual_grid_cost_sek", "actual_export_rev_sek",
        "sim_battery_soc_pct", "sim_bat_charge_w", "sim_bat_discharge_w",
        "sim_grid_import_kwh", "sim_grid_export_kwh",
        "sim_cost_sek", "sim_export_rev_sek",
        "ref_grid_import_kwh", "ref_grid_export_kwh",
        "ref_cost_sek", "ref_export_rev_sek",
    ]

    def _n(v) -> str:
        """Formatera tal med komma som decimaltecken."""
        return str(v).replace(".", ",")

    with open(out_path, "w", newline="", encoding="utf-8-sig") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()

        for ts in timestamps:
            row = pivot[ts]

            # Prisschema: nuvarande + kommande 24h
            window_end = ts + timedelta(hours=24)
            window_prices = [(t, p) for t, p in all_prices if t >= ts and t < window_end]
            ps = build_price_schedule(window_prices, hourly_solar_w, ts, s, slot_minutes=slot_minutes)

            state = build_state(row, ps, s, ts)

            day_plan = None
            if ps and ps.slots:
                try:
                    day_plan = planner.build_plan(
                        now=ts,
                        battery_soc_pct=state.battery_soc_pct,
                        battery_capacity_kwh=state.battery_capacity_kwh,
                        battery_max_power_kw=state.battery_max_power_kw,
                        ps=ps,
                        predicted_daily_kwh=_predicted_daily_kwh_at(ts, state.outdoor_temp_c),
                        solar_forecast_tomorrow_kwh=state.solar_forecast_tomorrow_kwh,
                        solar_takeover_dt=None,
                        house_load_w=state.house_load_w,
                        battery_avg_cost_sek_kwh=state.battery_avg_cost_sek_kwh,
                        yesterday_consumption_kwh=state.yesterday_consumption_kwh,
                        house_load_avg_w=state.house_load_avg_w,
                        load_shape_p50=_load_shape_at(ts, 0.5),
                        load_shape_p75=_load_shape_at(ts, 0.75),
                        current_outdoor_temp_c=state.outdoor_temp_c if drought_oracle else None,
                        weather_forecast=_oracle_weather_forecast(ts) if drought_oracle else None,
                    )
                except Exception as _plan_err:
                    sys.stdout.buffer.write(f"DayPlan-fel vid {ts}: {_plan_err}\n".encode("utf-8"))

            if day_plan:
                _cs = day_plan.slot_at(ts)
                state.plan_action = _cs.action if _cs else None
                state.plan_export_floor_kwh = day_plan.export_floor_kwh

            decision = controller.compute(state)
            solar_surplus_w = max(0.0, state.solar_power_w - state.house_load_w)
            decision = controller.apply_plan_executor(
                day_plan, "auto", state, decision, ts, solar_surplus_w,
            )

            # Faktisk näteffekt (W) -> energi för DENNA slot (slot_h, inte alltid en timme)
            slot_h = slot_minutes / 60.0
            grid_w = (state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3)
            grid_kwh = grid_w / 1000.0 * slot_h

            import_kwh = max(0.0,  grid_kwh)
            export_kwh = max(0.0, -grid_kwh)
            cost_sek   = import_kwh * state.buy_price_sek_kwh
            rev_sek    = export_kwh * state.sell_price_sek_kwh

            # Modellvalidering: samma energibalans, men med historikens EGNA
            # uppmätta batterieffekt i stället för SEM:s beslut – jämförs mot
            # grid_kwh (uppmätt) ovan, inte mot sim_*.
            if replay_soc is None:
                replay_soc = state.battery_soc_pct
            replay_charge_w    = max(0.0,  state.battery_power_w)
            replay_discharge_w = max(0.0, -state.battery_power_w)
            replay_grid_w = (state.house_load_w + replay_charge_w
                              - replay_discharge_w - state.solar_power_w)
            total_replay_import_kwh += max(0.0,  replay_grid_w / 1000.0 * slot_h)
            total_replay_export_kwh += max(0.0, -replay_grid_w / 1000.0 * slot_h)
            replay_soc = _simulate_battery_soc(
                replay_soc, replay_charge_w, replay_discharge_w, slot_h,
                s["battery_capacity_kwh"], s["eta_roundtrip"],
            )

            # ── P7-2: kör samma pipeline en gång till, men med en själv-
            # integrerad SOC i stället för historikens verkliga SOC, så att
            # SEM:s egna tidigare beslut faktiskt påverkar nästa slots
            # startläge. Sol och huslast är fortfarande de verkliga
            # historiska mätvärdena – ingen väder-/lastmodell finns.
            if sim_soc is None:
                sim_soc = state.battery_soc_pct  # startvillkor: verklig SOC vid periodens start
            if sim_prev_battery_power_w is None:
                sim_prev_battery_power_w = state.battery_power_w  # startvillkor: verklig effekt

            # battery_power_w sätts till FÖREGÅENDE simulerade beslut, inte
            # historikens verkliga effekt: _apply_phase_limits() läser den
            # som "nuvarande batterieffekt" för att räkna ut en delta mot
            # beslutet (rad ~980 i energy_controller.py). Läcker den verkliga
            # historiska effekten in här blir den deltan inkonsekvent så fort
            # den simulerade banan avviker från historiken.
            sim_state = dataclasses.replace(state, battery_soc_pct=sim_soc,
                                             battery_power_w=sim_prev_battery_power_w,
                                             plan_action=None, plan_export_floor_kwh=None)
            sim_day_plan = None
            if ps and ps.slots:
                try:
                    sim_day_plan = planner.build_plan(
                        now=ts,
                        battery_soc_pct=sim_soc,
                        battery_capacity_kwh=state.battery_capacity_kwh,
                        battery_max_power_kw=state.battery_max_power_kw,
                        ps=ps,
                        predicted_daily_kwh=_predicted_daily_kwh_at(ts, state.outdoor_temp_c),
                        solar_forecast_tomorrow_kwh=state.solar_forecast_tomorrow_kwh,
                        solar_takeover_dt=None,
                        house_load_w=state.house_load_w,
                        battery_avg_cost_sek_kwh=sim_avg_cost_sek_kwh,
                        yesterday_consumption_kwh=state.yesterday_consumption_kwh,
                        house_load_avg_w=state.house_load_avg_w,
                        load_shape_p50=_load_shape_at(ts, 0.5),
                        load_shape_p75=_load_shape_at(ts, 0.75),
                        current_outdoor_temp_c=state.outdoor_temp_c if drought_oracle else None,
                        weather_forecast=_oracle_weather_forecast(ts) if drought_oracle else None,
                    )
                except Exception as _sim_plan_err:
                    sys.stdout.buffer.write(f"Sim DayPlan-fel vid {ts}: {_sim_plan_err}\n".encode("utf-8"))
            if sim_day_plan:
                _sim_cs = sim_day_plan.slot_at(ts)
                sim_state.plan_action = _sim_cs.action if _sim_cs else None
                sim_state.plan_export_floor_kwh = sim_day_plan.export_floor_kwh

            sim_decision = controller.compute(sim_state)
            sim_solar_surplus_w = max(0.0, sim_state.solar_power_w - sim_state.house_load_w)
            sim_decision = controller.apply_plan_executor(
                sim_day_plan, "auto", sim_state, sim_decision, ts, sim_solar_surplus_w,
            )

            # Nätbalans DERIVERAD ur energibalansen (inte uppmätt) – den
            # simulerade SOC-banan avviker normalt från historiken, så den
            # verkliga fasavläsningen gäller inte längre för detta scenario.
            sim_grid_w = (state.house_load_w
                          + sim_decision.battery_charge_power_w
                          - sim_decision.battery_discharge_power_w
                          - state.solar_power_w)
            sim_import_kwh = max(0.0,  sim_grid_w / 1000.0 * slot_h)
            sim_export_kwh = max(0.0, -sim_grid_w / 1000.0 * slot_h)
            sim_cost_sek   = sim_import_kwh * state.buy_price_sek_kwh
            sim_rev_sek    = sim_export_kwh * state.sell_price_sek_kwh

            # Snittkostnad (SEK/kWh) för den lagrade energin, samma princip
            # som BatteryAccumulatedCostSensor (sensor.py): sol till
            # batteri bokförs till säljpris (alternativkostnad), nät till
            # köppris. Uppdateras bara vid laddning - urladdning ändrar inte
            # snittkostnaden, bara hur mycket energi den gäller för (se
            # kommentaren vid variabelns deklaration).
            if sim_decision.battery_charge_power_w > 50:
                _old_kwh = sim_soc / 100.0 * s["battery_capacity_kwh"]
                _charge_kwh = sim_decision.battery_charge_power_w / 1000.0 * slot_h
                _solar_kwh = min(_charge_kwh, max(0.0, sim_solar_surplus_w) / 1000.0 * slot_h)
                _grid_kwh = _charge_kwh - _solar_kwh
                _charge_cost_sek = _solar_kwh * state.sell_price_sek_kwh + _grid_kwh * state.buy_price_sek_kwh
                _new_kwh = _old_kwh + _charge_kwh
                if _new_kwh > 0.01:
                    sim_avg_cost_sek_kwh = (sim_avg_cost_sek_kwh * _old_kwh + _charge_cost_sek) / _new_kwh

            new_sim_soc = _simulate_battery_soc(
                sim_soc, sim_decision.battery_charge_power_w,
                sim_decision.battery_discharge_power_w, slot_h,
                s["battery_capacity_kwh"], s["eta_roundtrip"],
            )

            # Referens: samma energibalans helt utan batteri/styrning – vad
            # huset hade kostat med bara sol och nät, ingen lagring.
            ref_grid_w = state.house_load_w - state.solar_power_w
            ref_import_kwh = max(0.0,  ref_grid_w / 1000.0 * slot_h)
            ref_export_kwh = max(0.0, -ref_grid_w / 1000.0 * slot_h)
            ref_cost_sek   = ref_import_kwh * state.buy_price_sek_kwh
            ref_rev_sek    = ref_export_kwh * state.sell_price_sek_kwh

            total_sim_import_kwh   += sim_import_kwh
            total_sim_export_kwh   += sim_export_kwh
            total_sim_cost_sek     += sim_cost_sek
            total_sim_rev_sek      += sim_rev_sek
            if sim_decision.battery_charge_power_w > 50:
                total_sim_charge_kwh += sim_decision.battery_charge_power_w / 1000.0 * slot_h
            elif sim_decision.battery_discharge_power_w > 50:
                total_sim_discharge_kwh += sim_decision.battery_discharge_power_w / 1000.0 * slot_h
            total_ref_import_kwh   += ref_import_kwh
            total_ref_export_kwh   += ref_export_kwh
            total_ref_cost_sek     += ref_cost_sek
            total_ref_rev_sek      += ref_rev_sek

            sim_soc_this_slot = sim_soc
            sim_soc = new_sim_soc
            sim_prev_battery_power_w = (sim_decision.battery_charge_power_w
                                         - sim_decision.battery_discharge_power_w)

            total_grid_import_kwh  += import_kwh
            total_grid_export_kwh  += export_kwh
            total_grid_cost_sek    += cost_sek
            total_export_rev_sek   += rev_sek
            total_solar_kwh        += state.solar_power_w / 1000.0 * slot_h
            if state.battery_power_w > 50:
                total_bat_charge_kwh   += state.battery_power_w / 1000.0 * slot_h
                n_charge_hours         += 1
            elif state.battery_power_w < -50:
                total_bat_discharge_kwh += abs(state.battery_power_w) / 1000.0 * slot_h
                n_discharge_hours       += 1
            if export_kwh > 0.05:
                n_export_hours += 1

            writer.writerow({
                "timestamp":               ts.isoformat(),
                "spot_sek_kwh":            _n(round(state.spot_price_sek_kwh, 4)),
                "buy_sek_kwh":             _n(round(state.buy_price_sek_kwh, 4)),
                "sell_sek_kwh":            _n(round(state.sell_price_sek_kwh, 4)),
                "solar_w":                 _n(round(state.solar_power_w, 0)),
                "house_load_w":            _n(round(state.house_load_w, 0)),
                "battery_soc_pct":         _n(round(state.battery_soc_pct, 1)),
                "battery_power_w":         _n(round(state.battery_power_w, 0)),
                "grid_kw":                 _n(round(grid_w / 1000.0, 2)),
                "plan_action":             state.plan_action or "",
                "export_floor_kwh":        _n(round(day_plan.export_floor_kwh, 2)) if day_plan else "",
                "decision_bat_charge_w":   _n(round(decision.battery_charge_power_w, 0)),
                "decision_bat_discharge_w":_n(round(decision.battery_discharge_power_w, 0)),
                "decision_extra_hot_water":decision.extra_hot_water,
                "reason":                  decision.reason,
                "actual_grid_import_kwh":  _n(round(import_kwh, 3)),
                "actual_grid_export_kwh":  _n(round(export_kwh, 3)),
                "actual_grid_cost_sek":    _n(round(cost_sek, 4)),
                "actual_export_rev_sek":   _n(round(rev_sek, 4)),
                "sim_battery_soc_pct":     _n(round(sim_soc_this_slot, 1)),
                "sim_bat_charge_w":        _n(round(sim_decision.battery_charge_power_w, 0)),
                "sim_bat_discharge_w":     _n(round(sim_decision.battery_discharge_power_w, 0)),
                "sim_grid_import_kwh":     _n(round(sim_import_kwh, 3)),
                "sim_grid_export_kwh":     _n(round(sim_export_kwh, 3)),
                "sim_cost_sek":            _n(round(sim_cost_sek, 4)),
                "sim_export_rev_sek":      _n(round(sim_rev_sek, 4)),
                "ref_grid_import_kwh":     _n(round(ref_import_kwh, 3)),
                "ref_grid_export_kwh":     _n(round(ref_export_kwh, 3)),
                "ref_cost_sek":            _n(round(ref_cost_sek, 4)),
                "ref_export_rev_sek":      _n(round(ref_rev_sek, 4)),
            })

    net_cost = total_grid_cost_sek - total_export_rev_sek
    slot_h = slot_minutes / 60.0
    actual_note = (
        "" if has_actual_power_data
        else "  (INTE tillgängligt - historik saknar nätfas-/batteri-inout-data)"
    )
    summary = (
        "\n"
        "=================================================\n"
        "  SAMMANFATTNING\n"
        "=================================================\n"
        f"  Period         : {timestamps[0].date()} - {timestamps[-1].date()}\n"
        f"  Slots          : {len(timestamps)} à {slot_minutes} min ({len(timestamps) * slot_h:.1f}h totalt)\n"
        "\n"
        f"  Natimport (verklig)     : {total_grid_import_kwh:8.1f} kWh ({total_grid_cost_sek:.2f} SEK){actual_note}\n"
        f"  Natexport (verklig)     : {total_grid_export_kwh:8.1f} kWh ({total_export_rev_sek:.2f} SEK intakt){actual_note}\n"
        f"  Nettokostnad (verklig)  : {net_cost:8.2f} SEK\n"
        f"  Sol (faktisk)           : {total_solar_kwh:8.1f} kWh\n"
        f"  Bat laddning (verklig)  : {total_bat_charge_kwh:8.1f} kWh ({n_charge_hours * slot_h:.1f}h){actual_note}\n"
        f"  Bat urladdning (verklig): {total_bat_discharge_kwh:8.1f} kWh ({n_discharge_hours * slot_h:.1f}h){actual_note}\n"
        f"  Exportslots (planerad)  : {n_export_hours}\n"
        "\n"
        f"  Resultat -> {out_path}\n"
        "=================================================\n"
    )
    sys.stdout.buffer.write(summary.encode("utf-8"))

    # ── P7-2: framåtsimulerad batterimodell vs referens utan batteri/styrning ──
    n_days = max(1e-9, len(timestamps) * slot_h / 24.0)
    sim_net_cost = total_sim_cost_sek - total_sim_rev_sek
    ref_net_cost = total_ref_cost_sek - total_ref_rev_sek
    savings_sek  = ref_net_cost - sim_net_cost
    savings_pct  = (savings_sek / ref_net_cost * 100.0) if ref_net_cost > 0 else 0.0
    full_cycles  = total_sim_discharge_kwh / s["battery_capacity_kwh"] if s["battery_capacity_kwh"] else 0.0
    final_sim_soc = sim_soc if sim_soc is not None else float("nan")

    validation_note = ""
    if has_actual_power_data:
        def _pct_err(model_v: float, actual_v: float) -> float:
            return (model_v - actual_v) / actual_v * 100.0 if actual_v else float("nan")
        imp_err = _pct_err(total_replay_import_kwh, total_grid_import_kwh)
        exp_err = _pct_err(total_replay_export_kwh, total_grid_export_kwh)
        soc_err = (replay_soc - state.battery_soc_pct) if replay_soc is not None else float("nan")
        validation_note = (
            "\n"
            "  --- Modellvalidering: återspelar historikens EGNA beslut (P7-2 acceptanskrav) ---\n"
            "  (Ovanstående sim_*/besparing använder SEM:s NYA beslut och ska\n"
            "   avvika från historiken – det är poängen. Detta återspelar i\n"
            "   stället den uppmätta batterieffekten för att pröva om själva\n"
            "   modellen/energibalansen är trovärdig.)\n"
            f"  Återspelad import  : {total_replay_import_kwh:8.1f} kWh  (verklig: {total_grid_import_kwh:.1f} kWh, {imp_err:+.1f}%)\n"
            f"  Återspelad export  : {total_replay_export_kwh:8.1f} kWh  (verklig: {total_grid_export_kwh:.1f} kWh, {exp_err:+.1f}%)\n"
            f"  Återspelad SOC vid slutet : {replay_soc:5.1f} %  (verklig: {state.battery_soc_pct:.1f} %, {soc_err:+.1f} pp)\n"
        )
    else:
        validation_note = (
            "\n"
            "  Modellvalidering: INTE tillgänglig – denna källa saknar\n"
            "  nätfas-/batteri-inout-data att spela upp och jämföra mot.\n"
        )

    sim_summary = (
        "\n"
        "=================================================\n"
        "  P7-2: FRAMÅTSIMULERAD BATTERIMODELL\n"
        "=================================================\n"
        f"  Simulerad SOC vid periodens slut : {final_sim_soc:5.1f} %  (verklig SOC: {state.battery_soc_pct:.1f} %)\n"
        f"  Simulerad fullcykler              : {full_cycles:6.2f}\n"
        "\n"
        f"  Simulerad nätimport/-export : {total_sim_import_kwh:8.1f} kWh / {total_sim_export_kwh:8.1f} kWh\n"
        f"  Simulerad nettokostnad      : {sim_net_cost:8.2f} SEK  ({sim_net_cost / n_days:.2f} SEK/dygn)\n"
        "\n"
        f"  Referens utan batteri/styrning:\n"
        f"    Nätimport/-export : {total_ref_import_kwh:8.1f} kWh / {total_ref_export_kwh:8.1f} kWh\n"
        f"    Nettokostnad      : {ref_net_cost:8.2f} SEK  ({ref_net_cost / n_days:.2f} SEK/dygn)\n"
        "\n"
        f"  Besparing vs referens : {savings_sek:8.2f} SEK  ({savings_sek / n_days:.2f} SEK/dygn, {savings_pct:.1f}%)\n"
        f"{validation_note}"
        "=================================================\n"
    )
    sys.stdout.buffer.write(sim_summary.encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="SEM Backtest")
    parser.add_argument(
        "input",
        help=(
            "Antingen en CSV-fil i det gamla HA-logbook-formatet (timupplöst, "
            "se load_csv()) eller en mapp i P7-1-formatet (testdata/history/, "
            "kvartsupplösning – t.ex. 'testdata/history')."
        ),
    )
    parser.add_argument("--out",        default="backtest_result.csv")
    parser.add_argument("--min-soc",    type=float)
    parser.add_argument("--percentile", type=float)
    parser.add_argument("--date-from",  help="YYYY-MM-DD")
    parser.add_argument("--date-to",    help="YYYY-MM-DD")
    parser.add_argument(
        "--drought-oracle", action="store_true",
        help=(
            "Testverktyg för torkrisk-påslaget v2 (docs/v1_implementation_plan.md, "
            "\"Torkrisk-påslag från SMHI:s väderprognos\"): bygg weather_forecast "
            "från RIKTIG historisk väderdata (weather_oracle.csv i katalogen, byggd "
            "från Open-Meteo archive-api) istället för en arkiverad SMHI-prognos "
            "(finns inte). Ett orakel, INTE en riktig prognos-simulering — ger en "
            "optimistisk övre gräns, inte ett förväntat verkligt utfall."
        ),
    )
    args = parser.parse_args()

    overrides = {}
    if args.min_soc    is not None: overrides["battery_min_soc"]           = args.min_soc
    if args.percentile is not None: overrides["export_sell_percentile"]    = args.percentile

    date_from = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc) if args.date_from else None
    date_to   = datetime.fromisoformat(args.date_to  ).replace(tzinfo=timezone.utc) if args.date_to   else None

    run_backtest(args.input, args.out, overrides, date_from, date_to, drought_oracle=args.drought_oracle)


if __name__ == "__main__":
    main()
