import json, csv
from datetime import datetime

SRC = r"C:\Users\Fredrik\AppData\Local\Temp\claude\C--Users-Fredrik-Documents-smart-energy-manager-custom-components-smart-energy-manager\2ad5635c-ad14-4b01-9775-ff0c3135e633\scratchpad\openmeteo_full.json"
OUT = "C:/Users/Fredrik/Documents/smart_energy_manager/testdata/history_jan_apr2026/weather_oracle.csv"

# weather_code (WMO) visade sig för brett — flaggade "snowy" även för dagar
# med i praktiken obetydlig nederbörd (t.ex. 18-24 jan, 0.0-0.1 cm snöfall,
# men ändå god faktisk solproduktion 17,5/2,2/1,5/2,9/1,9/2,0 kWh de dagarna
# — inte en verklig torka). snowfall_sum (cm/dygn) är den faktiska,
# kontinuerliga siffran och matchar bättre den manuella verifieringen
# (docs/v1_implementation_plan.md, "Nya fynd som formar v2") — tröskel
# 0.5 cm skiljer de bekräftade riktiga händelserna (2,2/1,3/1,1/1,5/5,5/2,9/
# 2,0/8,0 cm) från brus (0,1 cm-dagar som ändå gav normal sol).
SNOW_THRESHOLD_CM = 0.5

with open(SRC, encoding="utf-8") as f:
    om = json.load(f)

d = om["daily"]
dates = d["time"]
tmax = d["temperature_2m_max"]
tmin = d["temperature_2m_min"]
snowfall = d["snowfall_sum"]

with open(OUT, "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(["date", "condition", "temp_max_c", "temp_min_c"])
    for i, ds in enumerate(dates):
        condition = "snowy" if snowfall[i] >= SNOW_THRESHOLD_CM else "other"
        w.writerow([ds, condition, tmax[i], tmin[i]])

print("weather_oracle.csv skriven,", len(dates), "rader")
print("snöiga dagar (snowfall_sum >=", SNOW_THRESHOLD_CM, "cm):", sum(1 for x in snowfall if x >= SNOW_THRESHOLD_CM))
