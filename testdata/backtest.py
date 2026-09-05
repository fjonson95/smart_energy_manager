#!/usr/bin/env python3
"""
SEM Backtest – replays historical sensor data through the FULL live decision
pipeline: EnergyPlanner.build_plan() -> EnergyController.compute() ->
EnergyController.apply_plan_executor() (the exact same method coordinator.py
calls in production, so this reflects real behavior, not just the standalone
EnergyController.compute() heuristics).

"Shadow mode", not a true forward simulation: battery_soc_pct at each hour
comes from the REAL historical SOC in the CSV, not from a simulated
trajectory following SEM's own decisions. It answers "what would SEM have
decided at this historical moment", not "what would the SOC curve have
looked like if SEM had been driving the whole time" (Etapp 7 / P7-2's full
ambition — a simplified battery model integrating the decisions forward —
is not implemented here).

Solar per price-slot is a p50 proxy built from the ACTUAL historical solar
reading at that time (no historical Solcast p10/p50 archive exists), with a
fixed haircut for p10 (SETTINGS["p10_haircut"]). Good enough to exercise the
floor/export logic, not a stand-in for the real forecast.

Two input formats, auto-detected from whether `input` is a file or a directory:

  - A CSV file (testdata/timdata/*.csv): the old HA-logbook-export format,
    hourly resolution, one row per {timestamp, entity_id, state}. The bundled
    files are small, sparse, manually-curated samples (tens of hours) — good
    for smoke-testing the pipeline, not for evaluating real savings.
  - A directory (testdata/history/, from P7-1): one CSV per quantity
    (solar/house-load/outdoor-temp/battery-SOC at hourly resolution, price at
    real quarter-hour resolution — matching production, where price slots are
    genuinely 15 minutes, not an assumed hour). The hourly quantities are
    forward-filled onto the price series' quarter-hour grid. This format has
    NO historical grid-phase readings, so the "actual" import/export/cost
    accounting in the summary is unavailable for it (reported as such) — only
    the decision/plan_action columns (what SEM would have chosen) are
    meaningful. See testdata/history/Series info.txt for exactly what period
    and quantities are available.

Usage (from repo root):
    python testdata/backtest.py testdata/timdata/htestdata1.csv [--out results.csv]
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
from custom_components.smart_energy_manager.const import NO_CAR_SELECTED

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
    "sensor.nordpool_kwh_se3_sek_3_10_0_2":                    ("nordpool_raw",           lambda v: v / 100.0),
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
}


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


def run_backtest(
    data_path: str,
    out_path: str,
    overrides: dict,
    date_from: Optional[datetime] = None,
    date_to:   Optional[datetime] = None,
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
        # P7-1's testdata/history/ has no historical grid-phase or
        # battery-inout readings (only house load, solar, outdoor temp,
        # battery SOC and price were extracted) – grid_power_l1/l2/l3 and
        # battery_power_w stay at their EnergyState default of 0, so the
        # "actual" import/export/cost/charge/discharge accounting below is
        # meaningless for this source. The decision/plan_action columns
        # (what SEM would have chosen) are still fully meaningful.
        has_actual_power_data = False
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

    fieldnames = [
        "timestamp", "spot_sek_kwh", "buy_sek_kwh", "sell_sek_kwh",
        "solar_w", "house_load_w", "battery_soc_pct", "battery_power_w",
        "grid_kw", "plan_action", "export_floor_kwh",
        "decision_bat_charge_w", "decision_bat_discharge_w",
        "decision_extra_hot_water", "reason",
        "actual_grid_import_kwh", "actual_grid_export_kwh",
        "actual_grid_cost_sek", "actual_export_rev_sek",
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
                        predicted_daily_kwh=0.0,
                        solar_forecast_tomorrow_kwh=state.solar_forecast_tomorrow_kwh,
                        solar_takeover_dt=None,
                        house_load_w=state.house_load_w,
                        battery_avg_cost_sek_kwh=state.battery_avg_cost_sek_kwh,
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


def main():
    parser = argparse.ArgumentParser(description="SEM Backtest")
    parser.add_argument(
        "input",
        help=(
            "Antingen en CSV-fil (testdata/timdata/*.csv, timupplösning) eller en "
            "mapp i P7-1-formatet (testdata/history/, kvartsupplösning – t.ex. "
            "'testdata/history')."
        ),
    )
    parser.add_argument("--out",        default="backtest_result.csv")
    parser.add_argument("--min-soc",    type=float)
    parser.add_argument("--percentile", type=float)
    parser.add_argument("--date-from",  help="YYYY-MM-DD")
    parser.add_argument("--date-to",    help="YYYY-MM-DD")
    args = parser.parse_args()

    overrides = {}
    if args.min_soc    is not None: overrides["battery_min_soc"]           = args.min_soc
    if args.percentile is not None: overrides["export_sell_percentile"]    = args.percentile

    date_from = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc) if args.date_from else None
    date_to   = datetime.fromisoformat(args.date_to  ).replace(tzinfo=timezone.utc) if args.date_to   else None

    run_backtest(args.input, args.out, overrides, date_from, date_to)


if __name__ == "__main__":
    main()
