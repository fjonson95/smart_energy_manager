import json, csv
from datetime import datetime, timezone

OUT = "C:/Users/Fredrik/Documents/smart_energy_manager/testdata/history_jan2026/"

with open(OUT + "_price_state.json", encoding="utf-8") as f:
    stats = json.load(f)

with open(OUT + "price_quarterhour.csv", "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(["timestamp", "spot_sek_kwh"])
    n = 0
    for s in stats:
        ts = datetime.fromtimestamp(s["start"] / 1000.0, tz=timezone.utc)
        spot_sek = s["state"] / 100.0
        for q in range(4):
            qts = ts.replace(minute=15 * q)
            w.writerow([qts.isoformat(), round(spot_sek, 4)])
            n += 1
print("price_quarterhour.csv skriven,", n, "rader (", len(stats), "timmar x4)")
