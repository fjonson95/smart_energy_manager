#!/usr/bin/env python3
"""Slår ihop två råkällor till en sammanhängande dygnsvis energitidslinje:

  - Nätägarens export/import (Export elnat.csv / Import elnat.csv,
    ";"-separerad, svensk decimalkomma) - används som facit för import/export
    eftersom det är elbolagets egna mätarvärden, inte växelriktarens.
  - Sungrow iSolarCloud "Monthly report"-export (testdata/Monthly report_*.csv)
    - används ENDAST för PV-produktion, ingen egen växelriktar-mätning av
      import/export tas med (nätägarens värden är facit för det).

Output: testdata/history/daily_energy_merged.csv
  date, pv_kwh, import_kwh, export_kwh, temp_c, load_kwh, pv_source

pv_kwh/load_kwh är None (tom cell) för dygn utan Sungrow-täckning
(före 2025-09-01) - import/export/temp finns för hela perioden.

Usage: python _merge_daily_energy.py
"""
import csv
import glob
import os
from datetime import date as D, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # testdata/
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_energy_merged.csv")


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


def main():
    exp = load_grid(os.path.join(BASE, "Export elnat.csv"))
    imp = load_grid(os.path.join(BASE, "Import elnat.csv"))
    sungrow = load_sungrow()

    all_dates = sorted(set(exp) | set(imp))
    start = D.fromisoformat(all_dates[0])
    end = D.fromisoformat(all_dates[-1])

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "pv_kwh", "import_kwh", "export_kwh", "temp_c", "load_kwh", "pv_source"])
        cur = start
        n = 0
        n_pv = 0
        while cur <= end:
            ds = cur.isoformat()
            sg = sungrow.get(ds)
            row = [
                ds,
                sg["pv"] if sg else "",
                imp.get(ds, {}).get("val", ""),
                exp.get(ds, {}).get("val", ""),
                exp.get(ds, {}).get("temp", imp.get(ds, {}).get("temp", "")),
                sg["load"] if sg else "",
                "sungrow" if sg else "",
            ]
            w.writerow(row)
            n += 1
            if sg:
                n_pv += 1
            cur += timedelta(days=1)

    print(f"{n} dygn skrivna ({start} .. {end}) -> {OUT}")
    print(f"  varav {n_pv} med direkt PV-täckning (Sungrow), {n - n_pv} bara import/export/temp (nätägare)")


if __name__ == "__main__":
    main()
