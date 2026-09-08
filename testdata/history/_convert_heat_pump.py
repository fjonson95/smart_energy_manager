#!/usr/bin/env python3
"""P7-1 helper: convert a raw HA 'history' CSV export (entity_id,state,
last_changed) for sensor.ivt_total_active_power into the same
timestamp_utc/mean/min/max shape _convert.py produces from a statistics
JSON dump, so backtest.py's load_history_dir() picks it up like any other
series in _HISTORY_DIR_SERIES.

The raw export mixes granularity: an early stretch of ~hourly readings
(beyond HA's ~10-day raw retention, likely exported from long-term
statistics) followed by a high-frequency tail (~10-15s apart, within raw
retention). Only the ~hourly stretch is used here - the tail is a
different resolution and would need its own (non-hourly) handling.

Usage: python _convert_heat_pump.py <in.csv> <out.csv>
"""
import sys
import csv
from datetime import datetime, timezone

in_path, out_path = sys.argv[1], sys.argv[2]

rows = []
with open(in_path, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        try:
            ts = datetime.fromisoformat(row["last_changed"].rstrip("Z")).replace(tzinfo=timezone.utc)
            v = float(row["state"])
        except (KeyError, ValueError):
            continue
        rows.append((ts, v))

rows.sort()

# Klipp vid första luckan under 5 min (300s) - där rådata-svansen börjar.
cut = len(rows)
for i in range(1, len(rows)):
    if (rows[i][0] - rows[i - 1][0]).total_seconds() < 300:
        cut = i
        break
hourly = rows[:cut]

with open(out_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["timestamp_utc", "mean", "min", "max"])
    for ts, v in hourly:
        w.writerow([ts.isoformat(), v, "", ""])

if hourly:
    print(f"{len(hourly)} rows, {hourly[0][0].isoformat()} .. {hourly[-1][0].isoformat()} -> {out_path}")
    if cut < len(rows):
        print(f"  ({len(rows) - cut} högfrekventa rader i svansen ignorerade, {rows[cut][0].isoformat()} och framåt)")
else:
    print("0 rows")
