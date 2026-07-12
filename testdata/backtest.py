#!/usr/bin/env python3
"""
SEM Backtest – replays historical sensor data and simulates EnergyController decisions.

Usage (from repo root):
    python testdata/backtest.py testdata/timdata/htestdata1.csv [--out results.csv]

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
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule, PriceSlot,
)

# ── Konfiguration från Current Setting.txt ────────────────────────────────────
SETTINGS = {
    "grid_fees_sek_kwh":        0.45,
    "energy_tax_sek_kwh":       0.536,
    "vat_rate":                 0.25,
    "sell_extra_revenue":       0.07,
    "battery_capacity_kwh":     33.0,
    "battery_max_power_kw":     8.0,
    "battery_min_soc":          20.0,
    "battery_max_soc":          99.0,
    "export_sell_percentile":   0.80,
    "export_min_solar_tomorrow_kwh": 5.0,
    "grid_scale":               1000.0,   # grid_power_unit = kW
    "battery_power_inverted":   True,
    "max_current_per_phase":    20,
    "grid_voltage":             230,
}

# Sensor entity_id → EnergyState field + transform
SENSOR_MAP = {
    "sensor.nordpool_kwh_se3_sek_3_10_0_2":                    ("nordpool_raw",           lambda v: v / 100.0),
    "sensor.el_forbruk_power_power":                            ("house_load_w",           float),
    "sensor.sungrow_sg12rt_active_generation":                  ("solar_w",                float),
    "sensor.sonnenbatterie_271100_state_battery_percentage_real":("battery_soc_pct",       float),
    "sensor.sonnenbatterie_271100_state_battery_inout":         ("battery_power_w_raw",    float),
    "sensor.elmatare_active_power_l1":                          ("grid_l1_kw",             float),
    "sensor.elmatare_active_power_l2":                          ("grid_l2_kw",             float),
    "sensor.elmatare_active_power_l3":                          ("grid_l3_kw",             float),
    "sensor.elmatare_current_l1":                               ("grid_cur_l1",            float),
    "sensor.elmatare_current_l2":                               ("grid_cur_l2",            float),
    "sensor.elmatare_current_l3":                               ("grid_cur_l3",            float),
    "sensor.solcast_pv_forecast_forecast_today":                ("solcast_today",          float),
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


def build_price_schedule(
    hourly_prices: list[tuple[datetime, float]],
    ref_ts: datetime,
    s: dict,
) -> Optional[PriceSchedule]:
    """Build a minimal PriceSchedule from hourly Nordpool prices."""
    gf  = s["grid_fees_sek_kwh"]
    et  = s["energy_tax_sek_kwh"]
    vat = s["vat_rate"]
    ex  = s["sell_extra_revenue"]

    slots: list[PriceSlot] = []
    for ts, spot in hourly_prices:
        buy  = (spot + gf + et) * (1 + vat)
        sell = spot + ex
        slot_start = ts
        slot_end   = ts + timedelta(hours=1)
        slots.append(PriceSlot(
            start=slot_start, end=slot_end,
            spot_sek=spot, buy_sek=buy, sell_sek=sell,
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


def build_state(row: dict[str, str], price_schedule: Optional[PriceSchedule], s: dict) -> EnergyState:
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
        active_car_name="Kia" if ev_connected else "unknown",
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
        export_sell_percentile      = s["export_sell_percentile"],
        export_min_solar_tomorrow_kwh = s["export_min_solar_tomorrow_kwh"],
    )

    sys.stdout.buffer.write(f"Laddar {data_path} ...\n".encode("utf-8"))
    pivot = load_csv(data_path)

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

    # Samla alla timpriser för prisschema
    all_prices: list[tuple[datetime, float]] = []
    for ts, row in pivot.items():
        raw = row.get("sensor.nordpool_kwh_se3_sek_3_10_0_2")
        if raw:
            v = _safe(raw, lambda x: float(x) / 100.0)
            if v is not None:
                all_prices.append((ts, v))
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
        "grid_kw",
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
            ps = build_price_schedule(window_prices, ts, s)

            state  = build_state(row, ps, s)
            decision = controller.compute(state)

            # Faktisk näteffekt (W)
            grid_w = (state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3)
            grid_kwh = grid_w / 1000.0  # per timme

            import_kwh = max(0.0,  grid_kwh)
            export_kwh = max(0.0, -grid_kwh)
            cost_sek   = import_kwh * state.buy_price_sek_kwh
            rev_sek    = export_kwh * state.sell_price_sek_kwh

            total_grid_import_kwh  += import_kwh
            total_grid_export_kwh  += export_kwh
            total_grid_cost_sek    += cost_sek
            total_export_rev_sek   += rev_sek
            total_solar_kwh        += state.solar_power_w / 1000.0
            if state.battery_power_w > 50:
                total_bat_charge_kwh   += state.battery_power_w / 1000.0
                n_charge_hours         += 1
            elif state.battery_power_w < -50:
                total_bat_discharge_kwh += abs(state.battery_power_w) / 1000.0
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
    summary = (
        "\n"
        "=================================================\n"
        "  SAMMANFATTNING\n"
        "=================================================\n"
        f"  Period         : {timestamps[0].date()} - {timestamps[-1].date()}\n"
        f"  Timmar         : {len(timestamps)}\n"
        "\n"
        f"  Natimport      : {total_grid_import_kwh:8.1f} kWh   ({total_grid_cost_sek:.2f} SEK)\n"
        f"  Natexport      : {total_grid_export_kwh:8.1f} kWh   ({total_export_rev_sek:.2f} SEK intakt)\n"
        f"  Nettokostnad   : {net_cost:8.2f} SEK\n"
        f"  Sol (faktisk)  : {total_solar_kwh:8.1f} kWh\n"
        f"  Bat laddning   : {total_bat_charge_kwh:8.1f} kWh   ({n_charge_hours}h)\n"
        f"  Bat urladdning : {total_bat_discharge_kwh:8.1f} kWh   ({n_discharge_hours}h)\n"
        f"  Exporttimmar   : {n_export_hours}\n"
        "\n"
        f"  Resultat -> {out_path}\n"
        "=================================================\n"
    )
    sys.stdout.buffer.write(summary.encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="SEM Backtest")
    parser.add_argument("input", help="CSV-fil (timdata eller realtidsdata)")
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
