#!/usr/bin/env python3
"""P7-1 helper: convert a raw HA 'history' CSV export (entity_id,state,
last_changed) for the Nordpool price sensor into the same
timestamp/spot_sek_kwh shape _convert_price.py produces from a JSON dump.

Unlike _convert_heat_pump.py, the FULL file is kept (no resolution cut) -
load_history_dir() drives its pivot timestamps directly off this series, so
mixed hourly/quarter-hour spacing across the file is fine; each price point
just becomes its own pivot row.

Usage: python _convert_nordpool_csv.py <in.csv> <out.csv>
"""
import sys
import csv

in_path, out_path = sys.argv[1], sys.argv[2]

rows = []
with open(in_path, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        try:
            spot_sek = float(row["state"]) / 100.0
        except (KeyError, ValueError):
            continue
        rows.append((row["last_changed"], spot_sek))

rows.sort(key=lambda r: r[0])

with open(out_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "spot_sek_kwh"])
    for ts, spot_sek in rows:
        w.writerow([ts, spot_sek])

if rows:
    print(f"{len(rows)} rows, {rows[0][0]} .. {rows[-1][0]} -> {out_path}")
else:
    print("0 rows")
