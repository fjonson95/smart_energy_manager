#!/usr/bin/env python3
"""P7-1 helper: convert a raw HA statistics JSON dump (list of
{start(ms), mean, min, max}) into a CSV with ISO timestamps, and print the
documented date range for that series.

Usage: python _convert.py <in.json> <out.csv> <label>
"""
import sys
import json
import csv
from datetime import datetime, timezone

in_path, out_path, label = sys.argv[1], sys.argv[2], sys.argv[3]

with open(in_path, encoding="utf-8") as f:
    rows = json.load(f)

with open(out_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["timestamp_utc", "mean", "min", "max"])
    for r in rows:
        ts = datetime.fromtimestamp(r["start"] / 1000.0, tz=timezone.utc).isoformat()
        w.writerow([ts, r.get("mean"), r.get("min"), r.get("max")])

if rows:
    start = datetime.fromtimestamp(rows[0]["start"] / 1000.0, tz=timezone.utc).isoformat()
    end = datetime.fromtimestamp(rows[-1]["start"] / 1000.0, tz=timezone.utc).isoformat()
    print(f"{label}: {len(rows)} rows, {start} .. {end} -> {out_path}")
else:
    print(f"{label}: 0 rows")
