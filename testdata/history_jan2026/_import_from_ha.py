import json, csv
from datetime import datetime, timezone

TR = "C:/Users/Fredrik/.claude/projects/C--Users-Fredrik-Documents-smart-energy-manager-custom-components-smart-energy-manager/2ad5635c-ad14-4b01-9775-ff0c3135e633/tool-results/"
files = {
    TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788961046819.txt": {
        "sensor.el_forbruk_power_power": "house_load_hourly.csv",
        "sensor.boiler_outdoortemp": "outdoor_temp_hourly.csv",
        "sensor.sonnenbatterie_271100_state_battery_percentage_real": "battery_soc_hourly.csv",
    },
    TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788961050142.txt": {
        "sensor.elmatare_active_power_l1": "grid_l1_hourly.csv",
        "sensor.elmatare_active_power_l2": "grid_l2_hourly.csv",
        "sensor.elmatare_active_power_l3": "grid_l3_hourly.csv",
        "sensor.sonnenbatterie_271100_state_battery_inout": "battery_inout_hourly.csv",
    },
    TR + "mcp-6598227f-9636-492d-ba60-ee3047d79f39-ha_get_history-1788961052708.txt": {
        "sensor.ivt_total_active_power": "heat_pump_power_hourly.csv",
        "sensor.nordpool_kwh_se3_sek_3_10_0_2": "__price__",
    },
}
OUT = "C:/Users/Fredrik/Documents/smart_energy_manager/testdata/history_jan2026/"

price_rows = []
for fpath, mapping in files.items():
    with open(fpath, encoding="utf-8") as f:
        d = json.load(f)
    entities = d["data"]["entities"]
    for ent in entities:
        eid = ent["entity_id"]
        outname = mapping.get(eid)
        if outname is None:
            print("SKIP unmapped", eid)
            continue
        stats = ent.get("statistics", [])
        rows = []
        for s in stats:
            if "mean" not in s:
                continue
            ts = datetime.fromtimestamp(s["start"] / 1000.0, tz=timezone.utc)
            rows.append((ts.isoformat(), s["mean"]))
        if outname == "__price__":
            price_rows = rows
            continue
        with open(OUT + outname, "w", newline="", encoding="utf-8") as out:
            w = csv.writer(out)
            w.writerow(["timestamp_utc", "mean", "min", "max"])
            for ts, mean in rows:
                w.writerow([ts, mean, "", ""])
        print(outname, len(rows), "rows")

print("price rows:", len(price_rows))
with open(OUT + "price_quarterhour.csv", "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(["timestamp", "spot_sek_kwh"])
    for ts_str, mean_ore in price_rows:
        ts = datetime.fromisoformat(ts_str)
        spot_sek = mean_ore / 100.0
        for q in range(4):
            qts = ts.replace(minute=15 * q)
            w.writerow([qts.isoformat(), round(spot_sek, 4)])
print("price_quarterhour.csv skriven,", len(price_rows) * 4, "rader")
