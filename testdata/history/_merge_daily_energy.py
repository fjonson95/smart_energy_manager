#!/usr/bin/env python3
"""Slår ihop fyra råkällor till en sammanhängande dygnsvis energitidslinje:

  - Elleverantörens export/import (Export elnat.csv / Import elnat.csv,
    ";"-separerad, svensk decimalkomma) - facit för import/export/temp,
    elleverantörens egna mätarvärden, inte växelriktarens uppskattning.
  - Sungrow iSolarCloud "Monthly report"-export (testdata/Monthly report_*.csv)
    - PV-produktion och hushållslast (elleverantören har ingen PV-kolumn).
  - Värmepumpens (IVT) egen effekt, timvis (heat_pump_power_hourly.csv,
    UTC) - aggregeras till dygnsmedel + ett grovt dygns-kWh-estimat
    (medel_W × 24 / 1000; hörnfall vid ofullständiga dygn ger en
    underskattning, inte en poäng-integration).
  - Nordpool spotpris, blandad tim-/kvartsupplösning
    (nordpool_price_extended.csv, UTC) - aggregeras till dygnsmedel/min/max.

Alla UTC-tidsstämplar konverteras till Europe/Stockholm INNAN de grupperas
per dygn, så dygnsgränserna matchar elleverantörens/Sungrows lokala dygn
(annars skulle t.ex. kvällstimmar UTC hamna på fel dygn under sommartid).

Output: testdata/history/daily_energy_merged.csv
  date, pv_kwh, import_kwh, export_kwh, temp_c, load_kwh, pv_source,
  heat_pump_avg_w, heat_pump_kwh_est, price_mean_sek_kwh, price_min_sek_kwh,
  price_max_sek_kwh, price_n_points

Tomma celler = ingen täckning för den källan det dygnet.

Usage: python _merge_daily_energy.py
"""
import csv
import glob
import os
from datetime import date as D, timedelta
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # testdata/
HIST = os.path.dirname(os.path.abspath(__file__))                   # testdata/history/
OUT = os.path.join(HIST, "daily_energy_merged.csv")
TZ = ZoneInfo("Europe/Stockholm")


def load_grid(fname):
    rows = {}
    with open(fname, encoding="utf-8-sig") as f:
        r = csv.reader(f, delimiter=";")
        next(r)
        next(r)
        for row in r:
            if len(row) < 5 or not row[1]:
                continue
            date = row[1].strip('"')
            val = row[2].strip('"').replace(",", ".")
            temp = row[4].strip('"').replace(",", ".")
            try:
                rows[date] = {
                    "val": float(val) if val else None,
                    "temp": float(temp) if temp else None,
                }
            except ValueError:
                pass
    return rows


def load_sungrow():
    rows = {}
    for fn in sorted(glob.glob(os.path.join(BASE, "Monthly report_028778 - Fredrik Jonson_*.csv"))):
        with open(fn, encoding="utf-8-sig") as f:
            r = csv.DictReader(f)
            for row in r:
                rows[row["Time"]] = {
                    "pv": float(row["PV(kWh)"]),
                    "load": float(row["Load(kWh)"]),
                }
    return rows


def load_heat_pump_daily():
    """timestamp_utc,mean,min,max -> {local_date: [mean, mean, ...]}"""
    path = os.path.join(HIST, "heat_pump_power_hourly.csv")
    by_day = {}
    if not os.path.exists(path):
        return by_day
    from datetime import datetime
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["timestamp_utc"])
                local_date = ts.astimezone(TZ).date().isoformat()
                v = float(row["mean"])
            except (KeyError, ValueError):
                continue
            by_day.setdefault(local_date, []).append(v)
    return by_day


def load_price_daily():
    """timestamp,spot_sek_kwh -> {local_date: [spot, spot, ...]}"""
    path = os.path.join(HIST, "nordpool_price_extended.csv")
    by_day = {}
    if not os.path.exists(path):
        return by_day
    from datetime import datetime
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["timestamp"].rstrip("Z")).replace(tzinfo=None)
                # Rå export är UTC ("...Z" eller redan naiv UTC) - annotera explicit.
                ts = ts.replace(tzinfo=ZoneInfo("UTC"))
                local_date = ts.astimezone(TZ).date().isoformat()
                v = float(row["spot_sek_kwh"])
            except (KeyError, ValueError):
                continue
            by_day.setdefault(local_date, []).append(v)
    return by_day


def main():
    exp = load_grid(os.path.join(BASE, "Export elnat.csv"))
    imp = load_grid(os.path.join(BASE, "Import elnat.csv"))
    sungrow = load_sungrow()
    hp_daily = load_heat_pump_daily()
    price_daily = load_price_daily()

    all_dates = sorted(set(exp) | set(imp))
    start = D.fromisoformat(all_dates[0])
    end = D.fromisoformat(all_dates[-1])

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "date", "pv_kwh", "import_kwh", "export_kwh", "temp_c", "load_kwh", "pv_source",
            "heat_pump_avg_w", "heat_pump_kwh_est",
            "price_mean_sek_kwh", "price_min_sek_kwh", "price_max_sek_kwh", "price_n_points",
        ])
        cur = start
        n = 0
        n_pv = n_hp = n_price = 0
        while cur <= end:
            ds = cur.isoformat()
            sg = sungrow.get(ds)
            hp_vals = hp_daily.get(ds)
            price_vals = price_daily.get(ds)

            hp_avg = sum(hp_vals) / len(hp_vals) if hp_vals else ""
            hp_kwh_est = (hp_avg * 24 / 1000.0) if hp_vals else ""

            row = [
                ds,
                sg["pv"] if sg else "",
                imp.get(ds, {}).get("val", ""),
                exp.get(ds, {}).get("val", ""),
                exp.get(ds, {}).get("temp", imp.get(ds, {}).get("temp", "")),
                sg["load"] if sg else "",
                "sungrow" if sg else "",
                hp_avg,
                hp_kwh_est,
                (sum(price_vals) / len(price_vals)) if price_vals else "",
                min(price_vals) if price_vals else "",
                max(price_vals) if price_vals else "",
                len(price_vals) if price_vals else "",
            ]
            w.writerow(row)
            n += 1
            if sg:
                n_pv += 1
            if hp_vals:
                n_hp += 1
            if price_vals:
                n_price += 1
            cur += timedelta(days=1)

    print(f"{n} dygn skrivna ({start} .. {end}) -> {OUT}")
    print(f"  PV/last (Sungrow): {n_pv} dygn")
    print(f"  Värmepump (heat_pump_power_hourly.csv): {n_hp} dygn")
    print(f"  Pris (nordpool_price_extended.csv): {n_price} dygn")


if __name__ == "__main__":
    main()
