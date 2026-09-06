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

Home Assistant custom integration (`custom_components/smart_energy_manager`) som styr energiflöden i hemmet baserat på spotpris (Nordpool), solproduktion (Solcast) och batteristatus. Projektet körs på en riktig HA-instans – det finns inga automatiska tester.

Tillhörande **SEM LM Bridge** (`C:\Users\Fredrik\Documents\sem_lm_bridge\bridge.py`) är ett separat Python-service på en VM med Ollama. Det publicerar laddningsbeslut via MQTT var 300:e sekund. Bridge-koden delas **inte** av detta repo och måste kopieras manuellt till VM:en efter ändringar.

---

## Repo-layout

```
custom_components/smart_energy_manager/
  coordinator.py        # DataUpdateCoordinator – hämtar state, kör controller, applicerar beslut
  energy_controller.py  # Ren logik – EnergyState → ControlDecision (inga HA-beroenden)
  price_scheduler.py    # Nordpool-schemaläggning, headroom, negativt-pris-logik
  sensor.py             # Alla HA-sensorer inkl. batterikostnadsackumulatorer
  legionella.py         # Legionella-schemaläggning för varmvatten
  select.py             # Bilvals-entitet per laddare
  switch.py / number.py # Manöverentiteter
  const.py              # Alla konstanter och CONFIG-nycklar
  config_flow.py        # UI-konfigurering i HA

www/
  sem-charger-card.js   # Custom Lovelace-kort för laddare (kopieras till HA /www/)
```

---

## Nyckelbegrepp

### Driftlägen (`operating_mode`)
- `auto` – normalt solöverskottsstyrt läge
- `winter` – billigast-timmar-laddning av batteri och bil
- `force_charge_ev` – tvångsladdning av bil
- `force_charge_battery` – tvångsladdning av batteri
- `manual` – inga automatiska beslut

### Beslutspipeline (var 30:e sekund)
1. `coordinator._async_update_data()` hämtar alla sensor-states
2. Bygger `EnergyState` med laddare, batteri, sol, priser
3. `EnergyController.compute()` returnerar `ControlDecision`
4. Coordinator applicerar beslut via HA-tjänster

### Fasströmsgräns
`20A × 3 faser × 230V = 13 800 W` total faskapacitet. `_apply_phase_limits()` i energy_controller ser till att summan håller sig under `max_current_per_phase`.

### Batterikostnad
- Solenergi till batteri bokförs till **säljpris** (alternativkostnad)
- Nätenergi till batteri bokförs till **köppris**
- Urladdning skriver ner kostnaden proportionellt: `cost *= new_energy / old_energy`
- `BatteryAccumulatedCostSensor` är master-ackumulatorn; `BatteryAveragePriceSensor` läser direkt från den (ingen egen ackumulator)

### Proaktiv absorption
Håller bara headroom (reducerar `battery_max_soc`) inför kommande negativt pris. Startar **inte** varmvatten eller EV-laddning proaktivt – det sker först när priset är faktiskt negativt eller redan passerat (`had_negative_today`).

### Proaktiv export – prisväktad dispatch över kväll + morgon
Exportfönstret inkluderar **båda** kväll och imorgon bitti. Gränsen är solproduktion (`solar_kw < 2 kW` per Solcast-slot) istället för ett tidsklocksklipp vid `solar_takeover_dt`. Natt- och tidiga morgonslots (sol < 2 kW) ingår alltid; middagsslots utesluts automatiskt.

Urladdningseffekten viktas mot aktuellt pris relativt summan av alla kvarvarande slots i fönstret:
```
discharge_w = (sell_price / sum(remaining_high_slot_prices)) × exportable_kwh / 0.25h × 1000
```
Om imorgon bittis pris är 1,20 kr och kvällens är 0,80 kr allokeras proportionellt mer kWh till morgonen – utan att den totala exporterade volymen förändras. Fallback (utan Solcast-data) använder `solar_takeover_dt` som klipp. Fallback till jämn fördelning om prissumman är noll.

**Absolut minimipris – trigger men inte dispatchfönster**: `export_min_sell_price_sek_kwh` (0,70 kr) fungerar som en extra trigger för enstaka slots under percentilen. Dispatchen (`_high_slots`) använder alltid percentiltröskeln, inte abs-minimum. Är det absoluta minimumet den enda orsaken till exporten och inga höga prisslots återstår i fönstret → `export_active = False`. Utan detta skulle `_effective_threshold = 0,70` skapa ett 8–10 timmar långt natt-fönster, batteri tömmes till min-SOC.

### Exportgolvets referenstid – låst till fönstrets start
`_hours_dark` i exportgolvsberäkningen (`_export_floor_kwh = _hours_dark × last + 2 kWh`) räknas från den **tidigaste prisslotens starttid** (eller nu, om vi redan passerat den) fram till solar takeover – **inte från klockan nu**. Utan denna fix krymper golvet under hela exportfönstret (t.ex. 3.5h × 1.05 kW ≈ 3.7 kWh extra utrymme per kväll), vilket gör att systemet exporterar mer än planerat och batteriet når Sonnenbatteriets 20%-minimum innan solen tar över.

### Observerad solar takeover – Store-format
`coordinator._takeover_store` (`{DOMAIN}_solar_takeover`) sparar:
```json
{"observations": [480.0, 481.5, ...], "last_obs_date": "2026-08-14"}
```
Vid uppstart återställs `_takeover_observed_today = True` om `last_obs_date` är idag – förhindrar dubbel-observation vid omstart. Bakåtkompatibelt med gammalt listformat (utan datum).

### Legionella – förfallokontroll på datum
`due`-kontrollen i `LegionellaManager.should_run_now()` jämför *datum*, inte exakt timantal:
```python
due = today >= (last_run + timedelta(days=interval_days)).date()
```
Hela förfallodagen räknas som tillgänglig. Kördes senast torsdag 14:00 → nästa förfallodag är torsdag om 7 dagar, och sol/billigt pris tidigt på morgonen den dagen räcker för att trigga.

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

**Det finns inga automatiska tester.** Verifiera alltid i en riktig HA-instans.

1. Redigera filer lokalt
2. Kopiera `custom_components/smart_energy_manager/` till HA via Samba/SCP
3. Starta om HA-integrationen (Developer Tools → YAML → Reload) eller starta om hela HA
4. Kontrollera loggar under `Settings → System → Logs` eller filtrera på `smart_energy_manager`
5. För kortändringar: kopiera `www/sem-charger-card.js` och hårdladda webbläsaren (Ctrl+Shift+R)

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
