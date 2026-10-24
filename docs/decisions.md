# Beslut

Daterade beslut och incidenter som styr hur koden ser ut. En post per beslut;
koden pekar hit i stället för att bära långa förklaringar inline.

---

## 2026-10-24 – Svar på öppna frågor inför fas B (HANDOFF.md avsnitt 8)

| # | Fråga | Beslut | Underlag |
|---|---|---|---|
| 1 | Hård reserv: fast 2 kWh + EV-marginal eller härledd från uppmätt underskott före soluppgång? | **Beslutas i fas B**, när DP:n kan backtestas med båda varianterna. Trackern (`_export_margin_*`, solar takeover) får ligga kvar och samla data tills dess. | – |
| 2 | Rundgångsverkningsgrad | **0,848** (uppmätt) i planering och backtest. Ändras i config och `testdata/backtest.py` först i fas B, så att baslinjen flyttas en gång. | Anläggningens AC-räknare, CHANGELOG 0.9.59 |
| 3 | Nedre SOC-gräns | **8 %** på sensorn `sensor.sonnenbatterie_271100_state_battery_percentage_real` ("verklig"). Användar-SOC-sensorn används inte. Config (idag 5) och backtest (idag 20) ändras i fas B. | Lägsta verkliga SOC 8 % (19–20 sep 2026); användar-SOC visade då 0 % |
| 4 | Arkivera Solcast per slot | **Ja**, fas A steg 2b. | – |
| 5 | `econ_peak`-vetot i exekutorn | **Tas bort i fas B** när exekutorn skrivs om till ren fysik. Ingen känd incident som det skyddar mot. | – |
| 6 | Nätavgift och köppris | **Ingen effekttariff**, fast överföringsavgift per kWh. Elhandel: Lerum Energi, rörligt kvartspris. Faktura sep 2026 (inkl. moms): elöverföring 38,95 öre, energiskatt 45,00 öre, rörliga kostnader 4,85 öre, fasta påslag 4,88 öre, spot × 1,25; nätnytta vid export 6,50 öre. Nordpool-sensorn är utan moms. ⇒ verkligt köp = **1,25 × spot + 0,937 kr/kWh**; konfigurationen ger 1,25 × spot + 1,2325 (0,45 och 0,536 är gamla värden). Rätt config exkl. moms: `grid_fees_sek_kwh` = 0,3116 + 0,0778 (elhandelns påslag) = **0,389**, `energy_tax_sek_kwh` = **0,36**. Säljsidan (spot + 0,065) stämmer. | Faktura 496467895 (2026-10-09), `docs/v1_implementation_plan.md` ("Det finns ingen effekttariff på abonnemanget") |
| 7 | EV-deadline | `input_datetime.kia_laddklar_senast` är konfigurerad men stod på ett passerat datum (2026-09-14 07:00). **Uppdateras manuellt när bilen behövs.** Ingen kodändring. | Config entry 2026-10-24 |
| 8 | Statistik för värmepump/golvvärme | **Redan på** för `boiler_hppower`, `boiler_curflowtemp`/`rettemp`, `thermostat_hc1_currtemp`, Shelly-kretsen och `ivt_total_active_power`. Bara `number.thermostat_hc1_seltemp` saknar långtidsstatistik. | Statistikuttag 2026-10-19/20 |

Inga HA-inställningar ändrades i samband med besluten. Köppriset (fråga 6),
min-SOC 8 % (fråga 3) och η 0,848 (fråga 2) ändras **tillsammans i fas B**, i
config och i `testdata/backtest.py`, så att baslinjen flyttas en gång.
