import csv, json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

JAN_DIR = "C:/Users/Fredrik/Documents/smart_energy_manager/testdata/history_jan2026/"
OUT = "C:/Users/Fredrik/Documents/smart_energy_manager/testdata/history_jan_apr2026/"
TR = "C:/Users/Fredrik/.claude/projects/C--Users-Fredrik-Documents-smart-energy-manager-custom-components-smart-energy-manager/2ad5635c-ad14-4b01-9775-ff0c3135e633/tool-results/"

# feb/mar/apr statistics tool-result files (group A: house_load/outdoor_temp/battery_soc,
# group B: grid_l1/l2/l3+battery_inout, group C: heat_pump_power)
MONTH_FILES = {
    "feb": {
        "A": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966275646.txt",
        "B": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966280206.txt",
        "C": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966282730.txt",
    },
    "mar": {
        "A": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966289288.txt",
        "B": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966295666.txt",
        "C": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966298546.txt",
    },
    "apr": {
        "A": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966304375.txt",
        "B": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966308314.txt",
        "C": TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788966310911.txt",
    },
}

ENTITY_TO_SERIES = {
    "sensor.el_forbruk_power_power": "house_load",
    "sensor.boiler_outdoortemp": "outdoor_temp",
    "sensor.sonnenbatterie_271100_state_battery_percentage_real": "battery_soc",
    "sensor.elmatare_active_power_l1": "grid_l1",
    "sensor.elmatare_active_power_l2": "grid_l2",
    "sensor.elmatare_active_power_l3": "grid_l3",
    "sensor.sonnenbatterie_271100_state_battery_inout": "battery_inout",
    "sensor.ivt_total_active_power": "heat_pump_power",
}

# series -> list of (timestamp_utc_iso, mean)
series_rows = {name: [] for name in ENTITY_TO_SERIES.values()}

for month, groups in MONTH_FILES.items():
    for group_key, fpath in groups.items():
        with open(fpath, encoding="utf-8") as f:
            d = json.load(f)
        entities = d["data"]["entities"]
        for ent in entities:
            eid = ent["entity_id"]
            name = ENTITY_TO_SERIES.get(eid)
            if name is None:
                print("SKIP unmapped", eid)
                continue
            for s in ent.get("statistics", []):
                if "mean" not in s:
                    continue
                ts = datetime.fromtimestamp(s["start"] / 1000.0, tz=timezone.utc)
                series_rows[name].append((ts.isoformat(), s["mean"]))

for name, rows in series_rows.items():
    rows.sort(key=lambda r: r[0])
    print(name, len(rows), "feb-apr rows")

# --- Load existing January rows from history_jan2026, prepend to feb-apr ---
JAN_FILE_MAP = {
    "house_load": "house_load_hourly.csv",
    "outdoor_temp": "outdoor_temp_hourly.csv",
    "battery_soc": "battery_soc_hourly.csv",
    "grid_l1": "grid_l1_hourly.csv",
    "grid_l2": "grid_l2_hourly.csv",
    "grid_l3": "grid_l3_hourly.csv",
    "battery_inout": "battery_inout_hourly.csv",
    "heat_pump_power": "heat_pump_power_hourly.csv",
}

for name, jan_fname in JAN_FILE_MAP.items():
    jan_rows = []
    with open(JAN_DIR + jan_fname, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            jan_rows.append((row["timestamp_utc"], float(row["mean"])))
    combined = jan_rows + series_rows[name]
    out_fname = jan_fname
    with open(OUT + out_fname, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["timestamp_utc", "mean", "min", "max"])
        for ts, mean in combined:
            w.writerow([ts, mean, "", ""])
    print(out_fname, len(combined), "total rows (jan", len(jan_rows), "+ feb-apr", len(series_rows[name]), ")")

# --- Price: combine jan price_quarterhour.csv with feb/mar/apr _price_state_*.json ---
jan_price_rows = []
with open(JAN_DIR + "price_quarterhour.csv", encoding="utf-8") as f:
    r = csv.DictReader(f)
    for row in r:
        jan_price_rows.append((row["timestamp"], float(row["spot_sek_kwh"])))

price_rows = list(jan_price_rows)
for month in ("feb", "mar", "apr"):
    with open(OUT + f"_price_state_{month}.json", encoding="utf-8") as f:
        stats = json.load(f)
    month_rows = []
    for s in stats:
        ts = datetime.fromtimestamp(s["start"] / 1000.0, tz=timezone.utc)
        spot_sek = s["state"] / 100.0
        for q in range(4):
            qts = ts.replace(minute=15 * q)
            month_rows.append((qts.isoformat(), round(spot_sek, 4)))
    month_rows.sort(key=lambda r: r[0])
    price_rows.extend(month_rows)
    print(month, "price rows:", len(month_rows))

with open(OUT + "price_quarterhour.csv", "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(["timestamp", "spot_sek_kwh"])
    for ts, spot in price_rows:
        w.writerow([ts, spot])
print("price_quarterhour.csv total:", len(price_rows), "rows")

# --- Solar: jan solar_hourly.csv + iSolarCloud feb/mar/apr exports (negative clamped to 0) ---
STHLM = ZoneInfo("Europe/Stockholm")
SOLAR_SRC = {
    "feb": r"C:\Users\Fredrik\Downloads\Curve_028778 - Fredrik Jonson_20260909153649.csv",
    "mar": r"C:\Users\Fredrik\Downloads\Curve_028778 - Fredrik Jonson_20260909154726.csv",
    "apr": r"C:\Users\Fredrik\Downloads\Curve_028778 - Fredrik Jonson_20260909154743.csv",
}

jan_solar_rows = []
with open(JAN_DIR + "solar_hourly.csv", encoding="utf-8") as f:
    r = csv.DictReader(f)
    for row in r:
        jan_solar_rows.append((row["timestamp_utc"], float(row["mean"])))

solar_rows = list(jan_solar_rows)
clamped_counts = {}
for month, src in SOLAR_SRC.items():
    with open(src, encoding="utf-8") as f:
        lines = f.readlines()
    clamped = 0
    month_rows = []
    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue
        ts_str, kw_str = line.split(",")
        local_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=STHLM)
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
        kw = float(kw_str)
        if kw < 0:
            kw = 0.0
            clamped += 1
        watts = kw * 1000.0
        month_rows.append((utc_dt.isoformat(), watts))
    clamped_counts[month] = clamped
    solar_rows.extend(month_rows)
    print(month, "solar rows:", len(month_rows), "clamped negative->0:", clamped)

with open(OUT + "solar_hourly.csv", "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(["timestamp_utc", "mean", "min", "max"])
    for ts, watts in solar_rows:
        w.writerow([ts, watts, "", ""])
print("solar_hourly.csv total:", len(solar_rows), "rows")
print("first:", solar_rows[0], "sist:", solar_rows[-1])
print("clamped counts:", clamped_counts)
