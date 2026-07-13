# Smart Energy Manager – HACS Integration

![Version](https://img.shields.io/badge/version-0.5.8-blue)

En HACS-integration för Home Assistant som optimerar egenförbrukning av solenergi med batteri, EV-laddare och elpanna/varmvattenberedare.

## Nyheter i 0.5.9

- **Proaktiv export: absolut minimipris** – nytt batteriinställning `export_min_sell_price_sek_kwh` (standard 0,70 SEK/kWh). När säljpriset når eller överstiger detta värde laddar batteriet ur och exporterar till nätet oavsett relativ percentilposition. Löser problemet där alla timmar på dygnet har enhetligt höga priser, vilket gör att percentiltröskeln aldrig uppnås trots lönsamma säljpriser. Sätt till 0 för att inaktivera.
- **Proaktiv export: percentil beräknas bara på dagens priser** – prispercentilen som avgör om export ska ske beräknas nu enbart från dagens prisslottar (tidigare inkluderades morgondagens priser, vilket kunde höja tröskeln när båda dagarna hade höga priser).

## Nyheter i 0.5.8

- **Fix: etiketter i konfigurations-UI** – alla steg i config flow har nu korrekta svenska och engelska etiketter. Laddare- och bilkonfigurationsstegen (`charger_menu`, `charger`, `car_menu`, `car` och deras options-motsvarigheter) använde tidigare fel steg-ID:n (`ev_menu`/`ev_car`) som inte matchade det faktiska flödet, vilket gjorde att fälten visade råa nyckelnamn istället för läsbara etiketter. Alla fält – laddarens namn, anslutningssensor, laddström-setpunkt, bilens antal inbyggda faser m.m. – visas nu korrekt.
- **Backtestramverk** – lade till `testdata/backtest.py` för att spela upp historisk sensordata genom energikontrollern. Läser timdata/realtidsdata-CSV:er, rekonstruerar `EnergyState` vid varje tidsstämpel, kör `EnergyController.compute()` och skriver en semikolonseparerad CSV med komma som decimaltecken (Excel-kompatibel). Stöder datumfiltrering och parameteröverstyrning (`--min-soc`, `--percentile`).

## Nyheter i 0.5.7

- **Batterikostnadsredovisning** – tre nya sensorer spårar ackumulerad kostnad för energin som finns i batteriet:
  - `sensor.sem_battery_accumulated_cost` – total kostnad (SEK) för energin i batteriet. Nätenergi prissätts till aktuellt köppris; solenergi prissätts till aktuellt säljpris (alternativkostnad – du hade kunnat sälja den istället). Vid urladdning minskas kostnaden proportionellt.
  - `sensor.sem_battery_average_price` – genomsnittlig kostnad per kWh i batteriet (SEK/kWh). Alltid beräknad direkt från kostnadssensorn för att vara konsistent.
  - Diagnostikattribut på kostnadssensorn: `solar_kwh_total`, `grid_kwh_total`, `last_sell_price`.
- **Fix: inverterat tecken på batterieffekt** – ny konfigurerbar inställning "Inverterat tecken på batterieffekt" under Batteri-inställningar (aktivera för Sonnenbatterie som rapporterar positivt = urladdning). Utan detta alternativ multiplicerades ackumulerad kostnad uppåt vid varje laddnings-/urladdningscykel och gav orealistiskt höga snittpriser. Efter att inställningen aktiverats, nollställ räknaren med den nya servicen `smart_energy_manager.reset_battery_cost` via Developer Tools → Services.
- **Proaktiv export** – batteriet laddar ur och exporterar till nät när aktuellt säljpris är vid eller över en konfigurerbar percentil av dagens priser, förutsatt att morgondagens Solcast-prognos överstiger ett minimiantal. Konfigurerbart under Batteri-inställningar (`export_sell_percentile`, `export_min_solar_tomorrow_kwh`).
- **Proaktiv absorption omarbetad** – när negativa elpriser väntas inom 2 timmar håller systemet nu bara headroom i batteriet (upp till 30%). Extra varmvatten och proaktiv EV-laddning **startas inte längre i förväg** – de väntar tills priset faktiskt är negativt. Efter att en negativ prisperiod passerat idag erbjuds extra varmvatten fortfarande när pannan behöver det.
- **Sol-aktiv laddningsbegränsning** – när solproduktionen överstiger 100 W laddas batteriet aldrig utöver det faktiska solöverskottet; nätström dras aldrig in i batteriet medan solen producerar.
- **Urladdning blockeras när sol täcker lasten** – om solproduktionen redan täcker hela huslasten tvingas inte batteriet till urladdning, även vid höga priser, eftersom inget nätköp sker ändå.

## Nyheter i 0.5.6

- **Säljprismedveten batteriladdning** – när säljpriset är lika med eller överstiger en konfigurerbar gräns (standard 0,80 SEK/kWh) exporteras solöverskottet till elnätet istället för att lagras i batteriet.
- **Solcast-baserad kvällsfylling** – systemet jämför återstående Solcast-prognos fram till solnedgång mot hur mycket energi batteriet fortfarande behöver för att nå konfigurerat lägsta SOC inför natten (`evening_min_soc`, standard 90 %). Om prognosen inte räcker laddas batteriet från sol (eller nät) även när säljpriserna är höga. Eftersom Solcast-prognosen är kalibrerad mot den faktiska panelplatsen och vinkeln fungerar detta korrekt under alla årstider utan någon fast klocktid.
- **Fix: batteriet väntar inte längre på sol under aktiv produktion** – tidigare blockerade "vänta på sol"-flaggan batteriladdning hela dagen när stor sol väntades inom 2 timmar. Spärren aktiveras nu bara när aktuell solproduktion understiger 500 W (solen producerar ännu inte).
- **Sol-integration** – `sun_next_setting` och `sun_next_rising` läses från `sun.sun`-entiteten och läggs till `EnergyState` för kvällsfyllningsberäkningen.

## Nyheter i 0.5.5

- **Solcast 30-minutersprognos integrerad** – `detailedForecast`-attributet från Solcast-sensorerna matchas mot Nordpools prisslottar vilket ger prisschemat per-slot soldata. Nya beslut: hoppa över batteriladdning från elnät om stor sol väntas inom 2 timmar, och skapa extra headroom i batteriet inför soltopp. Sex nya sensorer exponeras (se [Entiteter](#entiteter)).

## Nyheter i 0.5.4

- **Automatisk återställning av aktiv bil efter full laddning** – när en bil når sitt SOC-mål återställs bilvalet automatiskt till "unknown", så laddaren är redo för nästa session utan manuell åtgärd.

## Systemöversikt

```
Elnät (3-fas, max 20A/fas)
    │
    │    Batteri-inverter (3-fas)      Sol-inverter (3-fas)
    │           │                             │
    └─────┬─────┴─────────────────────────────┴──── Elmätare 4 (huslast) ────┬──── Övriga laster
          │                                                                  │
          │                                                              Elmätare 5
          │                                                                   │
          │                                                                Elpanna
          │                                                              (1-fas kompressor
          │                                                             +2-fas elpatron
          └────┐                                                        +ackumulatortank)
               │
           EV-laddare
        (1- eller 3-fas hårdvara,
         flera bilar turas om,
         bilval via HA-entitet)
```

> **OBS:** Laddarens fasantal (hårdvaran) och bilens fasantal (`car_phases`, 1/2/3-fas) är två separata inställningar. Fasbelastningen i fasskyddet styrs alltid av **bilens** inbyggda laddare, inte av laddarhårdvarans fasantal – se [EV-laddare och bilval](#ev-laddare-och-bilval).

### Elmätarroller

| Mätare | Placering | Tecken | Enhet |
|---|---|---|---|
| Elmätare 1 | Nätanslutning | Negativ = export, Positiv = import | kW |
| Elmätare 2 | Batteri-inverter AC-sida | Negativ = förbrukning | W |
| Elmätare 3 | Sol-inverter | Positiv = produktion | W |
| Elmätare 4 | Fastighetslast (exkl. sol, batteri och EV-laddare) | Positiv = förbrukning | W |
| Elmätare 5 | Elpanna | Positiv = förbrukning | W |

> **OBS:** Elm1 rapporterar i **kW** – välj enheten `kW` i konfigurationen. EV-laddare (intern mätare) rapporterar också i **kW**.

---

## Funktioner

### Autoläge (Självkonsumtion)
1. **Täck hushållslast** – prioritet 1
2. **Ladda bilar från sol** – när solöverskott ≥ 1 400 W (1-fas) eller 4 140 W (3-fas); ström justeras dynamiskt
3. **Ladda batteri från solöverskott**
4. **Extra varmvatten via elpatron** – när batteriet är fullt, sol finns kvar och tanktemperaturen är under konfigurerat max (standard 70°C)
5. **Ladda ur batteriet** – när köppriset överstiger 0,20 SEK/kWh, *eller* när det är den bästa urladdningstimmen kommande 12h enligt prisplaneringen
6. **Negativa elpriser** – absorbera all möjlig solel i batteri, bilar och varmvatten

### Prisplanering (kvartstimmar framåt)
Baserat på Nordpools `raw_today`/`raw_tomorrow`-attribut beräknas varje cykel:
- **Bästa laddnings-/urladdningstimme** kommande 12h – styr batteribeslut i både auto- och vinterläge
- **Proaktiv absorption** – om ≥ 4 kvartstimmar med negativt säljpris väntar inom 2h hålls headroom i batteriet (upp till 30%) för att ge plats åt solelen. Extra varmvatten och EV-laddning startas **inte** proaktivt; de väntar tills priset faktiskt är negativt
- Resultatet exponeras via `sensor.sem_negative_slots_ahead`, `sensor.sem_best_discharge_price` och `sensor.sem_best_charge_price`

### Solcast-prognos med 30-minutersupplösning
Om Solcast-sensorer är konfigurerade läses `detailedForecast`-attributet (30-minuters `pv_estimate`-värden i kW) varje cykel och matchas mot Nordpools prisslottar:
- **Sol per slot** – varje prissslott får ett förväntat soleffektvärde (kW) och energimängd (kWh)
- **Vänta på sol** – om ≥ 2 kWh sol väntas inom 2 timmar *och* aktuellt köppris överstiger 0,50 SEK/kWh hoppas batteriladdning från nät över för att spara utrymme åt gratis solel
- **Solaggregat** – rullande prognos för kommande 2 h, 4 h och 8 h exponeras som sensorer
- **Soltopp** – förväntad toppeffekt (kW) inom 8 h och antal timmar tills den inträffar
- `sensor.sem_wait_for_solar` slår på `on` när systemet håller tillbaka elnätsladdning i väntan på sol

### Vinterläge
- Ladda batteri nattetid när priset underskrider konfigurerbar gräns
- Ladda ur batteri under dyra timmar (kvällspeak)
- Ladda alltid från sol när möjligt

### Forcelägen
- **Force EV Charge** – ladda alla anslutna bilar från elnät (max ström, fasbegränsad)
- **Force Battery Charge** – ladda batteri från elnät

### Manuellt läge
- **Manual** – stänger av all automatisk styrning. Inga beslut fattas; används när du vill styra batteri/laddare/varmvatten manuellt via egna automationer. Väljs via `select.sem_operating_mode` (finns ingen egen switch för detta läget).

### Fasskydd
Beräknar fasbelastning per fas och reducerar i prioritetsordning:
1. Minskar EV-laddningsström (laddare med lägst prioritet först)
2. Minskar batteriladdning
3. Stänger av extra varmvatten (elpatron)

---

## EV-laddare och bilval

### Laddare → Bilar-modellen

Varje EV-laddare konfigureras som en **hårdvaruenhet** med en lista av bilar som kan använda den. När en bil ansluts väljer användaren vilken bil det är via en `select`-entitet i dashboarden.

```
Laddare A (3-fas hårdvara)
├── Anslutningssensor: sensor.charger_status
├── Bil 1: Volvo XC40    (SOC-sensor, mål 80%, 1-fas billaddare, fas L1)
└── Bil 2: Tesla Model 3 (SOC-sensor, mål 90%, 3-fas billaddare)
```

Fasbelastningen som fasskyddet räknar på bestäms alltid av **bilens** `car_phases` (1/2/3-fas), inte av laddarhårdvarans fasantal. En 1-fas bil belastar bara sin valda fas; en 2-fas bil belastar sin fas + nästa (t.ex. L1+L2); en 3-fas bil belastar alla tre faser lika.

### Bilvalsflöde

1. Bil ansluts → `sensor.sem_charger_a_connected` → `on`
2. Systemet pausar laddning och skickar en persistent HA-notifiering
3. Användaren väljer bil i `select.sem_charger_a_active_car`
4. Systemet laddar med rätt SOC-mål och fasinställning för den valda bilen
5. Notifieringen stängs automatiskt

### Konfiguration per laddare

| Inställning | Beskrivning |
|---|---|
| Namn | Visningsnamn för laddaren |
| Anslutningssensor | Sensor som visar `connected`/`charging`/`disconnected` |
| Laddare switch | Switch för att aktivera/avaktivera laddning |
| Laddström setpunkt | Number-entitet för strömsättning (A) |
| Laddeffekt sensor | Sensor för faktisk laddeffekt (valfri) |
| Antal faser | 1-fas eller 3-fas (hårdvara) |

### Konfiguration per bil

| Inställning | Beskrivning |
|---|---|
| Namn | Visningsnamn för bilen |
| SOC-sensor | Bilens batterisensor (valfri) |
| Mål-SOC | Laddningsmål i % (standard 80%) |
| Antal faser (bilens laddare) | 1-fas, 2-fas eller 3-fas – bilens **inbyggda** laddare, styr fasbelastningen |
| Fas | Startfas bilen laddar på (relevant vid 1- och 2-fas bilar) |

---

## Elpanna – fasmodell och temperaturstyrning

Elpannan har två separata kretsar:

| Krets | Drift | Faser | Typisk effekt |
|---|---|---|---|
| Värmepump (kompressor) | Normal husvärme | **1-fas** (konfigurerbar, standard L3) | 500–1 500 W |
| Elpatron | Extra varmvatten | **2-fas** (de två övriga faserna, standard L1+L2) | 3 000–6 000 W |

Patronfaserna beräknas automatiskt som de två faser som *inte* används av kompressorn.

### Temperaturstyrning för extra varmvatten

En temperatursensor på ackumulatortanken används för att förhindra onödig uppvärmning:

- **Max-temp** (standard 70°C): stänger av extra varmvatten om tanken når denna nivå
- **Min-temp** (standard 65°C): startar inte extra varmvatten förrän tanken är under denna nivå (även om allt annat tillåter det)
- Temperaturen visas i `sensor.sem_hot_water_temp`

### Minimitid (anti-flimmer)

För att förhindra att elpatronen slås på/av varje 30-sekunderscykel tvingas extra varmvatten att stanna PÅ i minst **5 minuter** (standard, konfigurerbart) från den faktiska starttidpunkten, även om styrlogiken vill stänga av det tidigare.

---

## Legionella-desinficering

Pannans inbyggda legionellaprogram körs automatiskt ca 1 gång/vecka för att värma varmvattnet till ≥ 65°C (konfigurerbart) och eliminera legionellabakterier.

### Hur det fungerar

Systemet använder en **separat digital switch** för att starta pannans legionellaprogram:

- Vi slår **PÅ** switchen för att starta programmet
- Pannan avslutar programmet och slår **AV** switchen automatiskt när klart
- Om switchen slås av i förtid avbryts cykeln och körningen räknas inte som lyckad
- Körningen bekräftas via temperatursensorn – når temperaturen inte målvärdet registreras körningen inte som lyckad

### Prioritetsordning för start

| Prioritet | Villkor | Beskrivning |
|---|---|---|
| 1 | Solöverskott ≥ 3 000 W inom önskat tidsfönster | Gratis solel driver programmet |
| 2 | Elpris ≤ konfigurerat maxpris inom önskat tidsfönster | Körs på billig nätström |
| 3 | Intervallet överskridits med 50% (nödkörning) | Kör oavsett pris, undviker natten 23–06 |

### Inställningar

| Inställning | Standard | Beskrivning |
|---|---|---|
| Aktiverad | Ja | Slå av/på funktionen |
| Legionella-switch | – | Pannans digitala programswitch |
| Bekräftelsetemp | 65°C | Temperatur som bekräftar lyckad körning |
| Intervall | 7 dagar | Hur ofta desinficering ska ske |
| Önskat tidsfönster | 10–15 | Timmar då sol normalt är tillgänglig |
| Max pris | 1,50 SEK/kWh | Kör ej på nätström om dyrare |
| Körtid | 60 min | Referenstid (pannan styr faktisk tid) |

---

## Prissättning

| | Formel |
|---|---|
| **Köppris** | `(spotpris + nätavgifter + energiskatt) × (1 + moms)` |
| **Säljpris** | `spotpris + extraintäkt (elcertifikat etc.)` |

---

## Entiteter

### Sensorer

| Entitet | Beskrivning |
|---|---|
| `sensor.sem_buy_price` | Aktuellt köppris SEK/kWh |
| `sensor.sem_sell_price` | Aktuellt säljpris SEK/kWh |
| `sensor.sem_spot_price` | Nordpool spotpris |
| `sensor.sem_battery_charge_power` | Batteri laddnings-setpoint (W) |
| `sensor.sem_battery_discharge_power` | Batteri urladdnings-setpoint (W) |
| `sensor.sem_phase_l1_load` | Beräknad fasbelastning L1 (W) |
| `sensor.sem_phase_l2_load` | Beräknad fasbelastning L2 (W) |
| `sensor.sem_phase_l3_load` | Beräknad fasbelastning L3 (W) |
| `sensor.sem_house_load` | Huslast W – Elm4 direkt eller beräknad |
| `sensor.sem_solar_surplus` | Solöverskott (W) |
| `sensor.sem_hot_water_temp` | Ackumulatortankens temperatur (°C) |
| `sensor.sem_decision_reason` | Textförklaring senaste beslut |
| `sensor.sem_operating_mode` | Aktivt driftläge |
| `sensor.sem_legionella_active` | `on` när legionellaprogrammet pågår |
| `sensor.sem_legionella_days_since` | Dagar sedan senaste bekräftade körning |
| `sensor.sem_legionella_next_due` | Datum för nästa planerad körning |
| `sensor.sem_legionella_temp_confirmed` | `on` om temp bekräftad under pågående körning |
| `sensor.sem_negative_slots_ahead` | Antal kvartstimmar med negativt säljpris kommande 8h |
| `sensor.sem_best_discharge_price` | Bästa (högsta) köppris för urladdning kommande 12h, med tidpunkt som attribut |
| `sensor.sem_best_charge_price` | Lägsta köppris för laddning kommande 12h, med tidpunkt som attribut |
| `sensor.sem_yesterday_consumption` | Gårdagens förbrukning exkl. EV-laddning (kWh) – kräver konfigurerad sensor |
| `sensor.sem_solar_next_2h_kwh` | Förväntad solenergi kommande 2 h (kWh, Solcast median) |
| `sensor.sem_solar_next_4h_kwh` | Förväntad solenergi kommande 4 h (kWh, Solcast median) |
| `sensor.sem_solar_next_8h_kwh` | Förväntad solenergi kommande 8 h (kWh, Solcast median) |
| `sensor.sem_peak_solar_kw_next_8h` | Förväntad toppeffekt från sol inom 8 h (kW) – attribut: `peak_solar_time` |
| `sensor.sem_hours_to_solar_peak` | Timmar tills soltoppen inom 8 h |
| `sensor.sem_wait_for_solar` | `on` när systemet håller tillbaka elnätsladdning i väntan på sol |
| `sensor.sem_battery_accumulated_cost` | Ackumulerad kostnad (SEK) för energin i batteriet – attribut: `solar_kwh_total`, `grid_kwh_total`, `average_price_sek_kwh`, `last_sell_price` |
| `sensor.sem_battery_average_price` | Genomsnittlig kostnad per kWh för energin i batteriet (SEK/kWh) |

**Per laddare** (ersätt `<laddare>` med laddarens namn i gemener):

| Entitet | Beskrivning |
|---|---|
| `sensor.sem_charger_<laddare>_connected` | `on`/`off` – bil fysiskt ansluten |
| `sensor.sem_charger_<laddare>_active_car` | Namn på vald bil |
| `sensor.sem_charger_<laddare>_current` | Laddström setpoint (A) |
| `sensor.sem_charger_<laddare>_enabled` | Laddning aktiv `on`/`off` |

> `sensor.sem_phase_lX_load` är en **prognos**, inte en mätning – den speglar beräknad fasbelastning *efter* att styrningsbesluten verkställts.

### Switches

| Entitet | Funktion |
|---|---|
| `switch.sem_force_ev_charge_from_grid` | Forcera EV-laddning från nät |
| `switch.sem_winter_mode` | Aktivera vinterläge |
| `switch.sem_force_charge_battery_from_grid` | Forcera batteriladdning |

### Select

| Entitet | Funktion |
|---|---|
| `select.sem_operating_mode` | Välj driftläge: `auto` / `winter` / `force_charge_ev` / `force_charge_battery` / `manual` |
| `select.sem_charger_<laddare>_active_car` | Välj vilken bil som är inkopplad på laddaren |

### Number (justerbart i realtid)

| Entitet | Funktion |
|---|---|
| `number.sem_battery_min_soc` | Batteri min SOC % |
| `number.sem_battery_max_soc` | Batteri max SOC % |
| `number.sem_ev_soc_target` | Global standard laddningsmål % |
| `number.sem_winter_cheap_threshold` | Prisgräns billigt (SEK/kWh) |
| `number.sem_winter_expensive_threshold` | Prisgräns dyrt (SEK/kWh) |
| `number.sem_winter_min_soc` | Vinter min SOC % |
| `number.sem_winter_max_soc` | Vinter max SOC % |

---

## Installation via HACS

1. Gå till HACS → Integrationer → ⋮ → Custom repositories
2. Lägg till `https://github.com/fjonson95/smart_energy_manager`, kategori: Integration
3. Installera "Smart Energy Manager"
4. Starta om Home Assistant
5. Inställningar → Integrationer → Lägg till → Smart Energy Manager

---

## Konfiguration

### Beroenden
Dessa HACS-integrationer måste vara installerade och konfigurerade:
- **nordpool** – elprissensor
- **solcast_solar** – solprognos (valfritt men rekommenderat)

### Konfigurationsflöde

Konfigurationen sker i sex steg:

**Steg 1 – Nät & Prissättning**
- Nordpool-sensor (obligatorisk)
- Nätmätare per fas (L1/L2/L3)
- Strömgivare per fas (för fasskydd)
- Max ström per fas (standard 20 A)
- Nätspänning (standard 230 V)
- Nätavgifter, energiskatt, moms, försäljningsersättning
- Huslaststyrare – peka på Elm4 för direkt huslastmätning (rekommenderas)
- Nätmätare enhet – välj `kW` om Elm1 rapporterar i kilowatt
- EV-laddare effektenhet – välj `kW` om laddaren rapporterar i kilowatt
- Gårdagens förbrukning – valfri sensor för `sensor.sem_yesterday_consumption`

**Steg 2 – Solceller**
- Sol-inverter total och per fas
- Solcast-prognoser idag/imorgon

**Steg 3 – Batteri**
- SOC-sensor, effektgivare, laddnings- och urladdningsentiteter
- Kapacitet (kWh) och max effekt (kW)
- Min/max SOC-gränser

**Steg 4 – Elpanna / Värmepump**
- Effektgivare (Elm5) – kompressorns på/av-status avläses från effekten, ingen separat switch behövs
- Switch för extra varmvatten (elpatron)
- Kompressorns fas (1-fas, standard L3) – patronfaserna beräknas automatiskt
- Elpatronens märkeffekt (kW)
- **Ackumulatortank temperatursensor** (valfri)
- **Max-temp för extra varmvatten** (standard 70°C)
- **Min-temp för extra varmvatten** (standard 65°C)
- **Minimitid extra varmvatten** (standard 5 min) – förhindrar flimmer på/av

**Steg 5 – Legionella-desinficering**
- Aktivera/avaktivera funktionen
- **Legionella-switch** (pannans digitala programswitch)
- **Bekräftelsetemp** (standard 65°C – körningen godkänns när tanken nått denna temp)
- Intervall i dagar (standard 7)
- Önskat tidsfönster (standard 10–15)
- Max elpris för körning på nätström (standard 1,50 SEK/kWh)
- Körtid i minuter (referenstid)

**Steg 6 – EV-laddare**
- Lägg till en eller flera laddare
- Per laddare: namn, anslutningssensor, switch, strömsättningsentitet, laddarens fasantal (1-fas eller 3-fas hårdvara)
- Per bil på laddaren: namn, SOC-sensor, SOC-mål, **bilens fasantal** (1/2/3-fas inbyggd laddare – styr fasbelastningen), fas (vid 1- och 2-fas bilar)
- Repetera för varje laddare

Alla inställningar kan redigeras i efterhand via **Inställningar → Integrationer → Smart Energy Manager → Konfigurera**.

### Enhetsanmärkning

| Sensor | Enhet i HA | Inställning |
|---|---|---|
| Elm1 (nätmätare) | kW | Nätmätare enhet → **kW** |
| Elm3 / SolInv_prod | W | (standard W) |
| BatInv_in_out | W, pos=laddning, neg=urladdning | (standard W) |
| Elm4 (huslast) | W | Huslaststyrare → Elm4 |
| Elm5 (elpanna) | W | Elpanna effektgivare → Elm5 |
| Bil_ladd (intern) | kW | EV-laddare effektenhet → **kW** |

---

## Loggning

```yaml
logger:
  logs:
    custom_components.smart_energy_manager: debug
```

---

## Exempel: Automation för vinterläge

```yaml
automation:
  - alias: "Winter mode October–March"
    trigger:
      - platform: time
        at: "00:01:00"
    condition:
      - condition: template
        value_template: "{{ now().month in [10,11,12,1,2,3] }}"
    action:
      - service: select.select_option
        target:
          entity_id: select.sem_operating_mode
        data:
          option: winter
```

## Exempel: Dashboard (Lovelace)

```yaml
type: vertical-stack
cards:
  - type: entity
    entity: select.sem_operating_mode
    name: Driftläge

  - type: glance
    entities:
      - entity: sensor.sem_buy_price
        name: Köp SEK/kWh
      - entity: sensor.sem_sell_price
        name: Sälj SEK/kWh
      - entity: sensor.sem_solar_surplus
        name: Solöverskott W
      - entity: sensor.sem_house_load
        name: Huslast W
      - entity: sensor.sem_hot_water_temp
        name: Varmvatten °C

  - type: gauge
    entity: sensor.sem_battery_charge_power
    name: Batteriladdning W
    max: 5000

  - type: entities
    title: Laddare A
    entities:
      - entity: sensor.sem_charger_laddare_a_connected
        name: Ansluten
      - entity: select.sem_charger_laddare_a_active_car
        name: Vald bil
      - entity: sensor.sem_charger_laddare_a_current
        name: Laddström A
      - entity: sensor.sem_charger_laddare_a_enabled
        name: Laddning aktiv

  - type: entities
    title: Legionella
    entities:
      - entity: sensor.sem_legionella_active
        name: Pågår
      - entity: sensor.sem_legionella_temp_confirmed
        name: Temp bekräftad
      - entity: sensor.sem_legionella_days_since
        name: Dagar sedan senaste
      - entity: sensor.sem_legionella_next_due
        name: Nästa körning

  - type: entity
    entity: sensor.sem_decision_reason
    name: Senaste beslut
```
