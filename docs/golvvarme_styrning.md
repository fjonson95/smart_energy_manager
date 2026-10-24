# Golvvärmestyrning – underlag och plan

Samlat underlag för att styra husets golvvärme mot elpris och solöverskott.
Hör ihop med **Steg 7 – Huset som värmelager** i `docs/v1_implementation_plan.md`.
Mätningar gjorda 2026-10-18/19 mot den skarpa HA-instansen. Ingen styrning
är införd i integrationen än (inga ändringar i `custom_components/`).

---

## 1. Sammanfattning

- Golvvärmen är **vattenburen**, en krets för hela huset (uppe + nere), styrd
  av värmepumpen (EMS-ESP, Bosch/Buderus/IVT AirModule) med **utetemperatur-
  styrd värmekurva**.
- Pannans framledning bestäms av **dämpad utetemperatur** och
  **`number.thermostat_hc1_seltemp`**. Husets temperatur återkopplas **inte**
  (`roominfluence` = 0). Återkopplingen sköts av de enskilda rumstermostaterna.
- Rätt hävstång för att flytta värme i tiden på **husnivå** är `seltemp`
  (och/eller `offsettemp`), inte rumstermostaterna.
- Mätt avsvalning i ett stängt rum: **~0,18–0,25 °C/h**. Återhämtning:
  **~0,13 °C/h** (ungefär hälften så snabbt). Rummet når inte börvärdet 20 °C
  vid nuvarande väder ens utan test.
- Golvvärmen drar **~2–3 kW nattetid** (pumpens egna räknare: 43 kWh på 16 h,
  COP ≈ 3,3–4,1). Det är den klart största flyttbara lasten i huset.
- Det som saknas: °C framledning per °C `seltemp`, svarstiden på husnivå och
  en kalibrerad effektsensor för golvvärmen.

---

## 2. Entiteter

### 2.1 Pannan / värmepumpen (EMS-ESP)

| Entitet | Roll | Anmärkning |
|---|---|---|
| `number.thermostat_hc1_seltemp` | **Rumsbörvärde, styrbart** | 19 °C sedan 17 okt. Hävstången. |
| `sensor.thermostat_hc1_currtemp` | Husets medeltemp enligt pannan | kommer från remotetemp |
| `number.thermostat_hc1_remotetemp` | Rumstemp skickad till pannan | skrivs av `automation.test` ("Remotetemp") var 5:e s från `sensor.avg_temp` |
| `climate.thermostat_hc1` | `hvac_action` (värmer/idle) | |
| `select.thermostat_hc1_controlmode` | `weather compensated` | |
| `select.thermostat_hc1_heatingtype` | `floor` | |
| `select.thermostat_hc1_mode` | `manual` (`manualtemp` = 19) | |
| `number.thermostat_hc1_designtemp` | 45 °C | kurvans slutpunkt |
| `number.thermostat_hc1_minflowtemp` / `maxflowtemp` | 25 / 59 °C | manualens fabriksvärde för max är 48 |
| `number.thermostat_hc1_roominfluence` | **0 (av)** | rumstemp påverkar inte kurvan |
| `sensor.thermostat_hc1_curroominfl` / `currsolarinfl` | 0 / 0 | |
| `number.thermostat_hc1_offsettemp` | 0 | parallellförskjutning av kurvan |
| `number.thermostat_hc1_summertemp` | 17 °C | värme av över detta (summersetmode `auto`) |
| `sensor.thermostat_dampedoutdoortemp` | Dämpad utetemp | indata till kurvan |
| `sensor.boiler_outdoortemp` | Aktuell utetemp | |
| `sensor.boiler_curflowtemp` / `boiler_rettemp` | Pannans egen fram/retur | brusiga (2–3 K ΔT) |
| `sensor.boiler_hppower` | Avgiven värme (kW) | |
| `sensor.boiler_nrgsuppheating` | Levererad värme golvvärme (kWh) | heltal, **sanning för energi** |
| `sensor.boiler_dhw_nrgsupp` | Levererad värme varmvatten (kWh) | |
| `sensor.boiler_nrgconscompheating` / `boiler_dhw_nrgconscomp` | El kompressor (golv / VV) | heltal |
| `sensor.ivt_total_active_power` | El till pumpen (W) | |
| `sensor.boiler_auxheaterstatus`, `boiler_auxheaterlevel` | Elpatron | **ska hållas av** |
| `number.boiler_tempparmode`, `boiler_auxheaterdelay` | Elpatronens parametrar | se Steg 7 punkt 2 |
| `number.boiler_pvmaxcomp` | Kompressorns tak vid PV | redan kopplad i `energy_controller.py` |

### 2.2 Golvvärmekretsen (Shelly Plus Uni)

| Entitet | Roll |
|---|---|
| `sensor.shellyplusuni_cc7b5c0a151c_temperature_2` | Framledning, hela golvvärmesystemet |
| `sensor.shellyplusuni_cc7b5c0a151c_temperature` | Retur, hela golvvärmesystemet |
| `sensor.shellyplusuni_cc7b5c0a151c_flode_pulse_counter_frequency_value` | Flöde (l/min), hela systemet (uppe + nere) |
| `sensor.shelly_plus_uni_varmeeffekt` | "Effekt Golvvärme" (W) |
| `sensor.golvvarme_nere_framledning/returledning/water_pressure` | Mätningar "nere" |
| `sensor.golv_uppe_*` | "uppe" – `unavailable` |

### 2.3 Rum

- Rumstermostater: `climate.*` (t.ex. `tv_rum`, `sovrum_r_f`, `sovrum_olivia`,
  `koket`, `hallen`, …). Attribut: `current_temperature`, `temperature`,
  `hvac_action`, `pulse_width_modulation` (värmeandel, 70–85 %), `status`.
- `status: rf_sensor_error` på **`sovrum_r_f`** och **`sovrum_olivia`** – den
  trådlösa givaren rapporterar fast värde (19,8 °C). Mätningar där är
  oanvändbara tills det är åtgärdat.
- Climate Scheduler (`climate.climate_scheduler_*`) styr schema för vissa rum
  (t.ex. `sovrum_r_f`: 17,6 °C, kort 18,8 °C kl 03:30–05:15). `tv_rum` ingick
  inte i något schema under testet.

---

## 3. Hur pannan räknar (manual AirModule 6 720 810 158, kap. 9.1.1, 12.3)

- **Utetemperaturstyrd** reglering: framledning = funktion av *dämpad*
  utetemperatur och rumsbörvärde.
- Värmekurva för golvvärme: baspunkt **25 °C vid +20 °C ute**, slutpunkt
  (`designtemp`) **45 °C vid lägsta dimensionerande utetemperatur** (DUT,
  fabriksvärde −10 °C), lätt böjd kurva.
- Kurvan i manualen gäller för **21 °C** rumstemperatur. Annat börvärde eller
  `offsettemp` ger **parallellförskjutning**.
- **Byggnadstyp (lätt/medel/tung)** styr hur starkt utetemperaturen dämpas.
  Tyngre hus = långsammare svar på utevädret.
- **Rumspåverkan** kan korrigera kurvan efter rumstemperatur (kräver
  rumsenhet) – hos oss avstängd.
- Indikation: vid 2–4 °C ute borde en 21 °C-kurva ge ~35–37 °C framledning;
  hos oss ligger den på 27–29 °C. Stämmer med att `seltemp` 19 sänker kurvan
  – men °C per grad är **ännu inte mätt**.

---

## 4. Mätresultat

### 4.1 Rumsstegtest – `climate.tv_rum` (börvärde 20 → 18 °C, 19 okt 01:00–11:00)

Rummet stängt för att undvika påverkan från andra rum. `sovrum_r_f` (ovanför)
kunde inte utvärderas (givarfel).

| Fas | Tid | Temp | Takt |
|---|---|---|---|
| Fördröjning | 01:00 → 02:53 | 19,8 → 19,7 | stilla ~2 h |
| Avsvalning | 03:16 → 07:34 | 19,6 → 18,6 (min) | ~0,25 °C/h som mest, snitt 0,18 °C/h |
| Stigning utan värme | 07:34 → 10:19 | 18,6 → 19,1 | sol/varmare ute |
| Återhämtning (börvärde 20) | 11:00 → 16:11 | 19,1 → 19,8 | ~0,13 °C/h, planar ut på 19,8 |

- Rummet nådde aldrig 18 °C → termostaten startade aldrig värme under natten.
- Före testet låg rummet redan på 19,6–19,8 °C mot börvärdet 20: vid det här
  vädret orkar kretsen inte upp till 20 °C.
- Baslinje 15–18 okt: ~80 % värmeandel vid 5–14 °C ute; solvärme 17 okt gav
  +2 °C (20,1 → 22,0 °C) utan värme; avsvalning 0,15–0,2 °C/h efteråt.
- Grov tidskonstant ~100 h (uppskattning, grannrum håller 20 °C).
- `pulse_width_modulation` gick 70 → 85 när börvärdet höjdes tillbaka.
- Testautomationen `sem_test_steg_test_tv_rum` är **borttagen**; `tv_rum` är
  åter på 20 °C.

### 4.2 Golvvärmekretsen 18 okt 20:00 – 19 okt 12:00

Timmedel (flöde i l/min), effekt = flöde × 4,186 × ΔT / 60:

| Tid | Fram | Retur | ΔT | Flöde | Effekt |
|---|---|---|---|---|---|
| 20 | 28,0 | 26,2 | 1,7 | 10,9 | 1,3 kW |
| 22 | 27,8 | 25,6 | 2,2 | 12,5 | 2,0 kW |
| 01 | 26,8 | 25,3 | 1,5 | 12,7 | 1,3 kW |
| 03 | 28,5 | 25,7 | 2,8 | 13,1 | 2,6 kW |
| 05 | 27,9 | 24,6 | 3,3 | 14,6 | 3,4 kW |
| 07 | 28,6 | 25,8 | 2,7 | 14,9 | 2,8 kW |
| 10 | 25,0 | 24,4 | 0,6 | 13,2 | 0,5 kW |

- Flödet nästan konstant (11–15 l/min) → pump med fast varvtal; effekten styrs
  av ΔT/framledning.
- Effekt 0,5–3,4 kW; ~30 kWh på 16 h (beräknat).
- Brus: ΔT 1–3 K, givarfel ±0,3–0,5 K ⇒ 20–30 % fel per timme.

### 4.3 Pannans egna räknare, samma 16 timmar

| Post | Värme | El |
|---|---|---|
| Golvvärme | **43 kWh** | 13 kWh |
| Varmvatten | 4 kWh (bara kl 20–22) | 2 kWh |
| Elpatron | 0 | 0 |

- `boiler_hppower` 49,7 kWh avgivet, `ivt_total_active_power` 12,6 kWh el.
- **COP** golvvärme 3,3 (räknare) – 4,1 (via `ivt_total_active_power` minus VV),
  medel ~3,7.
- Beräkningen från flöde × ΔT ger **0,67–0,69 ×** pumpens räknare, jämnt över
  natt och dag → systematiskt fel (flödesgivarens skalning eller ΔT-givarnas
  placering), ca faktor **1,45**.
- Natten 01–08: pumpen levererade 24 kWh golvvärme (3,4 kWh/h) mot
  2,0 kWh/h kvällen före. Kallaste intervallet; testet av `tv_rum` syns inte.
- Elpatronen var avstängd hela perioden.

### 4.4 Husets temperatur (`currtemp`)

Föll 21,0 → 20,1 °C över natten (≈0,9 °C på 8 h), sedan 20,2 °C. Samma
storleksordning som avsvalningen i `tv_rum`.

### 4.5 Husnivå-stegtest av `seltemp` (20 okt 02:00–05:00, 19 → 18 °C)

Ute ca −0,3 till −1,2 °C. 30-minutersmedel:

| Period | `currtemp` | Framledning | Pumpen avger | El | Ute |
|---|---|---|---|---|---|
| Före (00:30–02:00) | 20,5–20,6 | ~32,5 °C | 3,3 kW | ~0,9 kW | −0,3 |
| Under (02:30–05:00) | 20,35 → 19,9 | ~30,7 °C | 3,3–3,7 kW | ~0,85 kW | −0,5 till −1,1 |
| Efter (05:30–08:00) | 19,9 → 19,7 | ~32,2 °C | 3,3–5,4 kW | ~1,0 kW | −1,2 |

- Framledningen sjönk bara ~1–2 K (nära mätbruset); pumpens effekt oförändrad;
  `currtemp` föll lika snabbt före och under (~0,17 °C/h). **1 K är för lite
  för att ge ett mätbart skifte** – rumstermostaterna kompenserar.
- Kompressorn cyklar: står stilla ~30 min ungefär var tredje timme (00:00,
  02:00, 10:30), vilket ger hack i framledningen.
- `sensor.boiler_auxheaterstatus` har värdena `off`/`heating`, inte `on` –
  ett skydd som triggar på `on` fungerar aldrig. Använd `auxheaterlevel` > 0
  eller `ivt_total_active_power` över ~3 kW.
- Testet ska upprepas en kallare dag med större steg (2–3 K), se avsnitt 8.

---

## 5. Slutsatser för styrningen

1. **Styr på husnivå via `seltemp`/`offsettemp`**, inte via rumstermostaterna.
   Ett rum syns inte i kretsens effekt.
2. Lägre `seltemp` ⇒ lägre framledning ⇒ **bättre COP**. Rumstermostaterna
   håller ventilerna öppna längre och kompenserar delvis, så energin minskar
   inte lika mycket som temperaturen.
3. **Praktisk gräns:** ~1 °C sänkning håller ~5–6 h utan att värmen startar;
   återhämtningen tar ungefär dubbelt så lång tid. Sänkningar ska vara korta
   (några timmar) och återhämtningen ske i billiga timmar.
4. **Elpatronen får inte starta** under återhämtning (`auxheaterstatus`).
5. **Komfortgräns:** `currtemp` (eller `avg_temp`) får inte understiga t.ex.
   19,5 °C; säkerhetsfallback = återställ `seltemp` till normalvärdet.
6. Ekonomi: 1 kWh värme kostar ~0,25–0,3 kWh el (COP 3,5–4). Att flytta
   5 kWh värme från dyr till billig timme sparar prisskillnaden på ~1,3–1,4 kWh
   el minus COP-tappet vid högre framledning → `heat_planner.is_preheat_profitable()`.

---

## 6. Befintlig kod att bygga på (Steg 7)

I `custom_components/smart_energy_manager/heat_planner.py`:

| Funktion | Status |
|---|---|
| `is_preheat_profitable(price_peak, price_preheat, cop_preheat, cop_normal)` | klar, **ej kopplad** |
| `elpatron_avoidance_setpoints(...)` | klar, **ej kopplad** |
| `solar_compressor_boost_kw(...)` | **kopplad** (`energy_controller.py`, sätter `boiler_pvmaxcomp_kw`) |
| `should_dump_to_hot_water(...)` | **kopplad** (`energy_controller.py`) |
| `schedule_cheapest_window(...)` | klar, används ej (legionella löst separat i `legionella.py`) |

---

## 7. Förslag till implementation

1. **Mätning först (ingen styrning):** sensorer i integrationen:
   - golvvärmeeffekt (pumpens `nrgsuppheating`-delta, ev. flöde × ΔT × kalibrering),
   - värmepumpens COP (filtrerad, hppower / el),
   - golvvärmens andel av pumpens värme.
2. **Beslutslogik** (ren funktion, t.ex. i `heat_planner.py`): givet planens
   slotpriser, V och solöverskott → önskat `seltemp` per slot, med
   `is_preheat_profitable()` som lönsamhetsgrind. Begränsningar: max ±1–2 °C
   mot normalvärdet, minsta tid mellan ändringar, golv på `currtemp`.
3. **Utförande i `coordinator.py`:** `number.set_value` på
   `number.thermostat_hc1_seltemp`. Normalvärdet sparas i en Store och
   återställs vid avstängning/omstart/`manual`/`unavailable`. Elpatronsvakt.
4. **Gränssnitt:** switch (på/av), number (Δ, komfortgolv), statussensor med
   orsak (samma mönster som Legionella), översättningar sv/en, changelog +
   versionsbump enligt CLAUDE.md.
5. **Kopplas aldrig förrän** steg-testet i avsnitt 8 är gjort.

---

## 8. Nästa mätningar

1. **Husnivå-stegtest av `seltemp`:** 19 → 18 °C i 2–3 timmar kväll/natt.
   Logga `boiler_curflowtemp`, `hppower`, `nrgsuppheating`, `currtemp`,
   `auxheaterstatus`, utetemperatur. Ger: °C framledning per °C börvärde,
   svarstid, hur snabbt huset svalnar/återhämtar, ev. elpatronsrisk.
   Säkerhet: fallback till 19 om `currtemp` < 19,5 °C eller om elpatronen
   startar.
2. Flytta testet till en timme då spotpriset faktiskt är högt för att se
   ekonomin.
3. Kalibrera flödes-/ΔT-beräkningen mot pumpens räknare (pulsfaktor i Shelly,
   givarplacering).
4. Åtgärda `rf_sensor_error` på `sovrum_r_f` och `sovrum_olivia`.
5. Kontrollera pannans DUT, byggnadstyp (lätt/medel/tung) och om `maxflowtemp`
   59 °C är avsiktligt.
6. Rumsvis modell (tidskonstant/kWh per grad) – tidigast efter vintern
   (rumsgivarna har data bara sedan ~2026-07-25).

---

## 9. Öppna frågor

- Vilken del av pumpens värme går **inte** till golvvärmen enligt flödesmätningen
  (30 % glapp) – är det flödesskalningen eller ΔT?
- Vilket värde har DUT och byggnadstyp i pannan?
- Hur starkt svarar rumstermostaterna när framledningen sänks (blir
  energibesparingen liten?)
- Behöver `roominfluence` slås på för en säkrare reglering, eller räcker
  rumstermostaterna?
