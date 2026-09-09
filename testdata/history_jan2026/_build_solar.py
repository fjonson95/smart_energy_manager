import csv
from datetime import datetime
from zoneinfo import ZoneInfo

SRC = r"C:\Users\Fredrik\Downloads\Curve_028778 - Fredrik Jonson_20260909153511.csv"
OUT = "C:/Users/Fredrik/Documents/smart_energy_manager/testdata/history_jan2026/solar_hourly.csv"

STHLM = ZoneInfo("Europe/Stockholm")

rows = []
with open(SRC, encoding="utf-8") as f:
    lines = f.readlines()

# Rad 1 = rapportnamn, rad 2 = header ("Time,Inverter1(...)/Total active power(kW)")
for line in lines[2:]:
    line = line.strip()
    if not line:
        continue
    ts_str, kw_str = line.split(",")
    local_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=STHLM)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    watts = float(kw_str) * 1000.0
    rows.append((utc_dt.isoformat(), watts))

with open(OUT, "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(["timestamp_utc", "mean", "min", "max"])
    for ts, watts in rows:
        w.writerow([ts, watts, "", ""])

print("solar_hourly.csv skriven,", len(rows), "rader")
print("först:", rows[0], "sist:", rows[-1])
