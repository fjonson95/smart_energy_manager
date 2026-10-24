# Smart Energy Manager – CLAUDE.md

## Projektmål (prioritetsordning)

1. **Negativt säljpris & nattcykel** – två delar:
   - *Morgon-urladdning*: använd Solcast-prognos och gårdagens förbrukning för att bedöma om vi ska ladda ur batteriet när säljpriset är som högst (typiskt tidig morgon).
   - *Kvällsfylling (sommar)*: när solen slutar producera ska batteriet innehålla tillräcklig energi för att täcka husförbrukningen tills solen nästa dag producerar tillräckligt för att täcka huset – och dessutom ha marginal att hinna ladda upp batteriet inför nästa solcykel. Behovet beräknas dynamiskt från förbrukningsprognos och Solcast-prognos för morgondagen, inte från en fast SOC-gräns.
2. **Maximera egenförbrukning** – solenergi ska i första hand användas i huset och lagras i batteri, inte exporteras i onödan.
3. **Maximera vinst vid export** – när el ändå levereras till nätet ska det ske när spotpriset är högt (sälj dyrt, undvik att sälja billigt).
4. **Köp nätenergi till lägsta möjliga pris** – när batteriet behöver laddas från nätet ska det ske under de billigaste timmarna/kvartarna.
5. **Förbrukningsprognos** – gårdagens faktiska förbrukning kombinerat med temperaturdata används för att prediktera dagens förbrukning och fatta bättre laddningsbeslut.

---

## Vad är detta?

Home Assistant custom integration (`custom_components/smart_energy_manager`) som styr energiflöden i hemmet baserat på spotpris (Nordpool), solproduktion (Solcast) och batteristatus. Projektet körs på en riktig HA-instans. De rena modulerna testas med `pytest` (`tests/`, körs utan HA), resten verifieras skarpt.

Tillhörande **SEM LM Bridge** (`C:\Users\Fredrik\Documents\sem_lm_bridge\bridge.py`) är ett separat Python-service på en VM med Ollama. Det publicerar laddningsbeslut via MQTT var 300:e sekund. Bridge-koden delas **inte** av detta repo och måste kopieras manuellt till VM:en efter ändringar.

---

## Repo-layout

```
custom_components/smart_energy_manager/
  coordinator.py        # DataUpdateCoordinator – hämtar state, bygger plan, kör controller, applicerar beslut
  energy_planner.py     # Ren logik – DayPlan för 48 h (marginalvärdet V, regel 1–4, EV-schema)
  energy_controller.py  # Ren logik – EnergyState → ControlDecision; apply_plan_executor() skriver batteriet
  price_scheduler.py    # Nordpool-schemaläggning, headroom, negativt-pris-logik
  heat_planner.py       # Rena funktioner för värmepump/varmvatten (steg 7)
  savings_tracker.py    # Daglig besparing mot hus utan batteri
  sensor.py             # Alla HA-sensorer inkl. batterikostnadsackumulatorer
  legionella.py         # Legionella-schemaläggning för varmvatten
  select.py             # Bilvals-entitet per laddare
  switch.py / number.py # Manöverentiteter
  const.py              # Alla konstanter och CONFIG-nycklar
  config_flow.py        # UI-konfigurering i HA

www/
  sem-charger-card.js       # Lovelace-kort för laddare
  sem-energy-plan-card.js   # Lovelace-kort för dagsplanen
dist/smart-energy-manager-cards.js  # HACS-bundle av båda korten (byggs med build_hacs_bundle.py)

tests/                  # pytest utan HA (stub i conftest.py), inkl. sommarbacktesten
testdata/backtest.py    # Spelar upp historik genom planerare + controller + executor
docs/decisions.md       # Daterade beslut och flyttade incidentberättelser (P01…, C01…)
```

---

## Nyckelbegrepp

### Driftlägen (`operating_mode`)
- `auto` – planstyrt läge (DayPlan + exekutor); det finns inget separat vinterläge, säsong är indata
- `force_charge_ev` – tvångsladdning av bil
- `force_charge_battery` – tvångsladdning av batteri
- `manual` – inga automatiska beslut

### Beslutspipeline (var 30:e sekund)
1. `coordinator._async_update_data()` hämtar alla sensor-states
2. Bygger `EnergyState` med laddare, batteri, sol, priser
3. `EnergyPlanner.build_plan()` bygger om DayPlan (var 15:e min eller när priserna ändras)
4. `EnergyController.compute()` returnerar `ControlDecision` (EV, varmvatten, fasskydd, preliminärt batteri)
5. `EnergyController.apply_plan_executor()` ersätter batteridelen med planens slot, klämd mot verkligheten – enda stället som skriver batterieffekt (delas med backtesten)
6. Coordinator applicerar beslut via HA-tjänster

### Fasströmsgräns
`20A × 3 faser × 230V = 13 800 W` total faskapacitet. `_apply_phase_limits()` i energy_controller ser till att summan håller sig under `max_current_per_phase`.

### Batterikostnad
- Solenergi till batteri bokförs till **säljpris** (alternativkostnad)
- Nätenergi till batteri bokförs till **köppris**
- Urladdning skriver ner kostnaden proportionellt: `cost *= new_energy / old_energy`
- Kostnaden nollställs när SOC ≤ min-SOC + 5 punkter (Sonnen slutar urladdas runt 8 %, över den konfigurerade min-SOC), så fel inte ackumuleras cykel efter cykel
- `BatteryAccumulatedCostSensor` är master-ackumulatorn; `BatteryAveragePriceSensor` läser direkt från den (ingen egen ackumulator)

### Proaktiv absorption
Håller bara headroom (reducerar `battery_max_soc`) inför kommande negativt pris. Startar **inte** varmvatten eller EV-laddning proaktivt – det sker först när priset är faktiskt negativt eller redan passerat (`had_negative_today`).

### Batteriplanen – marginalvärdet V och regel 1–4
`energy_planner.build_plan()` allokerar batteriets energi i merit-ordning mot framtida möjligheter (täcka underskott till köppris, exportera till säljpris) inom effekt- och solgränser; V = värdet i billigaste tilldelade möjlighet × (1 + osäkerhetspåslag). Regler per slot: 1 lagra sol om `V_charge > sälj`; 2 täck huset om `köp > V + cykel` (inom slotens öronmärkning); 3 nätladda billigast först om `köp + cykel < V_charge`; 4 exportera om `sälj > max(V, batterikostnad) + cykel`. Den gamla percentil-exporten (exportgolv, `export_min_sell_price`, `_high_slots`) finns inte längre. Känd brist: planen kan hålla ett laddat batteri genom dyra timmar (HANDOFF.md 4.1, `tests/test_planner_probe.py`) – åtgärdas i fas B. Bakgrund per mekanism: `docs/decisions.md` P01–P16.

### Observerad solar takeover – Store-format
`coordinator._takeover_store` (`{DOMAIN}_solar_takeover`) sparar:
```json
{"observations": [480.0, 481.5, ...], "last_obs_date": "2026-08-14"}
```
Vid uppstart återställs `_takeover_observed_today = True` om `last_obs_date` är idag – förhindrar dubbel-observation vid omstart. Bakåtkompatibelt med gammalt listformat (utan datum).

### Legionella – rörligt intervall (5–9 dagar), billigaste fönstret
`LegionellaManager.should_run_now()` jämför *datum*: från `min`-dagen (standard 5) är det dags, och senast slutet av `max`-dagen (standard 9) körs det bästa kända fönstret oavsett pris. Mellan dem väljs billigaste körfönstret (körtid × **effektivt pris**) bland kvartarna i Nordpools kända prisdata, dygnets alla timmar. Effektivt pris = solöverskott (p10 minus husets last, mot 6 kW patron) värderat till säljpris, resten till köppris. Före max-dagen körs bara om bästa fönstret är ≤ 90 % av snittet i det kända; dagar bortom prishorisonten ses via Solcasts `forecast_day_3..7` (härledda ur imorgon-entiteten) och väntas in om de är minst 10 % billigare. Nödstart undviker natten 23–06. `next_due` = senaste körning + min-dagar.

### Nätladdningens solavdrag och planlogg
Regel 3 räknar nätladdningsbehov = utrymme till max − väntad gratis solpåfyllning. Påfyllningen använder `p10 + _GRID_CHARGE_SOLAR_BLEND × (p50 − p10)` (0,5); reserver, V och merit-ordningens utrymmeskontroll använder fortfarande ren p10. `coordinator._maybe_log_plan()` sparar fyra planögonblicksbilder per dygn (00/06/14/20, 36 h, motivering per slot) i `.storage/smart_energy_manager_plan_log` (senaste 40) – kopiera filen vid granskning istället för att gissa varför ett beslut togs.

### Daglig besparing och Solcast-arkiv
`savings_tracker.DailySavingsTracker` integrerar varje cykel nät- och batterieffekt till köp-/säljpris mot en referens utan batteri (`ref_nät = nät − batterieffekt`) → sensorn "Besparing idag" (`sem_daily_savings`), 30 dygn i `.storage/smart_energy_manager_daily_savings`. `coordinator._maybe_archive_solcast()` sparar Solcast p50/p10/p90 per slot efter 06 och 13 (60 dygn) i `.storage/smart_energy_manager_solcast_archive` – använd det, inte backtestens syntetiska p10, för att utvärdera prognosberoende beslut.

### EV-vakthund
Om bil är vald, laddning är beordrad men `charger_power < 50 W` i >5 minuter → `_active_cars[charger_name]` återställs till `NO_CAR_SELECTED`. Hanteras i `coordinator._build_charger_states()` via `_charge_command_times`.

### Bilval
`select.py` exponerar en `select`-entitet per laddare. `coordinator._active_cars[charger_name]` är master. Auto-rensas vid SOC-mål och av EV-vakthunden.

---

## Viktiga konstanter (const.py)

| Konstant | Värde | Beskrivning |
|---|---|---|
| `UPDATE_INTERVAL` | 30 s | Koordinatorns uppdateringsfrekvens |
| `NO_CAR_SELECTED` | `"unknown"` | Sentinel för "ingen bil vald" |
| `MIN_SOLAR_FOR_EV_1PHASE` | 1 400 W | Minsta solöverskott för 1-fas EV-laddning |
| `MIN_SOLAR_FOR_EV_3PHASE` | 4 140 W | Minsta solöverskott för 3-fas EV-laddning |
| `MIN_EV_CURRENT` | 6 A | Minsta tillåtna laddström |
| `MAX_EV_CURRENT` | 16 A | Maximalt laddström |
| `NEGATIVE_PRICE_THRESHOLD` | 0.0 SEK/kWh | Gräns för "negativt pris" |

---

## Utvecklingsflöde

Kör `python -m pytest` (≈10 s, inkl. sommarbacktesten som regressionstest) och båda backtesterna före varje commit; skriv siffrorna i CHANGELOG-posten. Vinterbacktesten körs med `--date-from 2026-01-01 --date-to 2026-04-30` (utan filter blir siffran en annan). Allt som rör HA-glue verifieras dessutom i en riktig HA-instans.

1. Redigera filer lokalt
2. Kopiera `custom_components/smart_energy_manager/` till HA via Samba/SCP
3. Starta om HA-integrationen (Developer Tools → YAML → Reload) eller starta om hela HA
4. Kontrollera loggar under `Settings → System → Logs` eller filtrera på `smart_energy_manager`
5. För kortändringar: kopiera `www/sem-charger-card.js`/`www/sem-energy-plan-card.js` och hårdladda webbläsaren (Ctrl+Shift+R). Kör även `python build_hacs_bundle.py` innan commit – HACS-plugin-resursen (`dist/smart-energy-manager-cards.js`) checkas in, byggs inte av HACS vid installation.

**För SEM LM Bridge:**
- Redigera `C:\Users\Fredrik\Documents\sem_lm_bridge\bridge.py` eller `backtest.py`
- Kopiera till VM: `scp bridge.py backtest.py olama@<VM-IP>:/opt/sem-lm-bridge/`
- Starta om service på VM: `sudo systemctl restart sem-lm-bridge`

---

## Kodkonventioner

- **Inga kommentarer** som förklarar vad koden gör – bara varför (dolda begränsningar, workarounds)
- **Inga abstraktioner i förtid** – tre likadana rader är bättre än en prematur helper
- Logga på svenska med `_LOGGER.info/warning/error`
- Pris i SEK/kWh genomgående
- Effekt alltid i Watt internt (skalning från kW sker i coordinator med `_grid_scale`/`_ev_scale`)
- `RestoreEntity` används för sensorer som behöver överleva HA-omstart

---

## Obligatoriska uppdateringar vid varje ändring

När du gör en funktionell ändring i integrationen **måste** du alltid:

1. **Uppdatera changelogen** – lägg till en "What's New in x.y.z" / "Nyheter i x.y.z"-punkt överst i **CHANGELOG.md och CHANGELOG.sv.md** om ändringen är användarsynlig, och bumpa versionsnumret (badge i README.md/README.sv.md + `manifest.json`). README.md/README.sv.md visar bara den SENASTE posten (ersätt den befintliga, kopiera inte in historiken) och länkar vidare till CHANGELOG.md/CHANGELOG.sv.md för äldre versioner – lägg aldrig en changelog-post direkt i README utan att också skriva den till CHANGELOG. Alla fyra filerna ska hållas i synk parvis (en på engelska, en på svenska).

2. **Uppdatera språkfiler** – om du lägger till eller byter namn på en konfigurationsnyckel, entitet, tjänst eller driftläge ska `translations/sv.json` och `translations/en.json` uppdateras med motsvarande text. Kontrollera att båda filerna har identiska nycklar.

Gör dessa uppdateringar i samma svar som koden – aldrig i efterhand.

---

## Vanliga fallgropar

- **Dropdown kollapsar i Lovelace-kortet**: `_update()` anropas vid varje HA-state-uppdatering – återskapa aldrig `innerHTML` om DOM-elementet redan finns
- **Solceller laddar batteri från nätet**: `_sanitize()` i bridge.py måste begränsa `battery_charge_w` till solöverskott när `solar_w > 100`
- **SOC-sensor opålitlig**: Lita inte blint på EV SOC – vakthunden hanterar fallet där bilen inte laddar trots kommando
- **Fasbalans**: Värmepatroner, sol-inverter och EV-laddare sitter på specifika faser – ändra aldrig fasval utan att uppdatera `_apply_phase_limits()`
- **`price_schedule` kan vara None**: Kontrollera alltid `if ps and ps.xxx` i energy_controller
