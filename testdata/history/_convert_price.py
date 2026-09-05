#!/usr/bin/env python3
"""P7-1 helper: convert raw HA history JSON dump (list of {state, last_changed})
for the Nordpool price sensor into a CSV of spot price in SEK/kWh at quarter-
hour resolution, and print the documented date range."""
import sys
import json
import csv

in_path, out_path = sys.argv[1], sys.argv[2]

with open(in_path, encoding="utf-8") as f:
    rows = json.load(f)

with open(out_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "spot_sek_kwh"])
    for r in rows:
        try:
            spot_sek = float(r["state"]) / 100.0
        except (ValueError, TypeError):
            continue
        w.writerow([r["last_changed"], spot_sek])

if rows:
    print(f"Spot price (öre->SEK/kWh, quarter-hour): {len(rows)} rows, "
          f"{rows[0]['last_changed']} .. {rows[-1]['last_changed']} -> {out_path}")
else:
    print("Spot price: 0 rows")
