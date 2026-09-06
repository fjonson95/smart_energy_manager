# Förbrukningsanalys — underlag för golv-/reservformeln

Arbetsdokument. Startade som en utredning av varför `export_floor_kwh` blev
orimligt högt (29,3 kWh av 30,7 kWh kapacitet) en solig dag i september, men
har växt till en bredare kartläggning av husets verkliga förbrukningsmönster.
Byggs vidare på i kommande sessioner — se "Öppna spår" längst ner.

## Bakgrund: varför den här analysen startade

2026-09-06 visade `sensor.smart_energy_manager_plan_anledning` ett golv på
29,3 kWh och `total_exportable_kwh: 0` trots 99% SOC och en solig dag med
75 kWh prognostiserad produktion. Två kandidatorsaker identifierades och
verifierades mot riktig kod + riktig data (se `energy_planner.py:build_plan()`):

1. **`hourly_load_kw` byggs på en momentan `house_load_w`-avläsning**
   (1350W vid det tillfället), inte på verklig historisk förbrukning —
   samma klass av bugg som redan fixades i `energy_controller.py` v0.7.6
   (`house_load_avg_w`), men **den fixen nådde aldrig `energy_planner.py`**.
   `state.yesterday_consumption_kwh` (verklig, uppmätt) skickas aldrig in
   till `build_plan()` överhuvudtaget.
2. **`pv_production_ratio`** (P3-2, 3-dygns produktionskvot) satte ett
   osäkerhetspåslag som var känsligt runt sin 0,9-tröskel — men historiken
   bakom kvoten visade sig ha färre riktiga dygn än designen förutsätter
   (funktionen är nyligen driftsatt).

Omräkning med den riktiga koden (`EnergyPlanner.build_plan()` körd direkt,
inte gissat): att byta momentanavläsningen (1350W) mot gårdagens faktiska
snitt (1095W) sänkte golvet från 23,4 → 19,1 kWh — en bekräftad, betydande
hävstång. Att därefter byta till den **verkligt uppmätta nattlasten** (se
nedan, ~830W sommartid) sänkte det vidare till 14,7 kWh — nästan halva
batteriet frigjort jämfört med det ursprungliga 29,3 kWh.

**Ingen kodändring är gjord ännu** — vi samlar underlag innan vi bestämmer
lösning (se `[[CLAUDE.md]]`-regeln "no changes until we understand" som
gällt hela den här utredningen).

## 1. Nattförbrukning, sommar — ren baslinje

Tre raka nätter (2026-09-03 till 09-06, 20:00–06:00 lokal tid), `sensor.el_forbruk_power_power`
i timstatistik. Ingen kontaminering av varmvatten/desinfektion/EV — alla sådana
händelser föll uteslutande på dagtid (08:00–17:00) i det här urvalet.

| Natt | Summa | Snitteffekt |
|---|---|---|
| 3→4 sep | 8,12 kWh | ~812 W |
| 4→5 sep | 8,59 kWh | ~859 W |
| 5→6 sep | 8,19 kWh | ~819 W |

**~830 W är den bästa tillgängliga siffran för "ofrånkomlig" nattlast sommartid.**

**Varför så stabilt:** enligt användaren är sommarens last "mer eller mindre
all IT-utrustning" — en konstant, dygnet-runt-last (server/nätverk), inte
väder- eller beteendeberoende som vanlig hushållsel. Förklarar variansen
(nästan ingen) bättre än "typisk hushållsbaslast" hade gjort.

Jämförelse med vad golvformeln faktiskt använde: momentanavläsning 1350W,
gårdagens dygnssnitt 1095W, verklig nattlast 830W — momentanavläsningen var
**63% högre** än verkligheten.

## 2. Pannans nedbrytning — kompressor vs elpatron, varmvatten vs rumsvärme

Fyra kompletterande `total_increasing`-räknare (permanent statistik, till
skillnad från switch-historik som rensas efter ~10 dygn) ger fullständig
uppdelning. Verifierat att de summerar exakt:

```
nrgconstotal (44124 kWh) = nrgconscomptotal (39594) + auxelecheatnrgconstotal (4530)
nrgconscomptotal = dhw_nrgconscomp (varmvatten) + nrgconscompheating (rumsvärme)
auxelecheatnrgconstotal = dhw_auxelecheatnrgcons (varmvatten) + auxelecheatnrgconsheating (rumsvärme)
```

| Sensor | Betydelse |
|---|---|
| `sensor.boiler_nrgconstotal` | Total elförbrukning, panna (kompressor + elpatron) |
| `sensor.boiler_nrgconscomptotal` | Kompressor, totalt |
| `sensor.boiler_nrgconscompheating` | Kompressor, rumsvärme |
| `sensor.boiler_dhw_nrgconscomp` | Kompressor, varmvatten |
| `sensor.boiler_auxelecheatnrgconstotal` | Elpatron, totalt |
| `sensor.boiler_auxelecheatnrgconsheating` | Elpatron, rumsvärme |
| `sensor.boiler_dhw_auxelecheatnrgcons` | Elpatron, varmvatten |
| `sensor.boiler_nrgsupptotal` / `nrgsuppheating` / `dhw_nrgsupp` | Levererad VÄRME (inte el) — ger verklig COP kombinerat med ovan |

**Fynd, sista halvan av januari (18–31 jan, se tabell i konversationen):**
pannans totala förbrukning låg på 32–42 kWh/dygn, löst korrelerat mot dämpad
utetemp (kallare → mer), men inte perfekt linjärt (även vid +2°C drogs 35 kWh
— en baslast finns även vid milt vinterväder). **Elpatronen för varmvatten var
i praktiken inaktiv hela perioden** (1636 → 1642 kWh, ett enda ~6 kWh-hopp
runt 27–28 jan, sen still). Nästan all förbrukning var kompressorn själv.

**Natten 20/2→21/2 (se avsnitt 4):** exakt 16 kWh över 10 timmar, **100%
kompressor, 0 kWh elpatron** (varken varmvatten- eller rumsvärme-räknaren
rörde sig), trots -2°C till 0°C dämpad utetemp. Kompressorn räckte själv.

**Öppen fråga:** vi har ännu inte hittat en period där elpatronen (rumsvärme
ELLER varmvatten) faktiskt varit aktiv i någon betydande omfattning, för att
se hur mycket den bidrar när den väl går igång.

## 3. EV-laddningsmönster — tre hittade tillfällen (februari)

Rå på/av-historik för laddare/switchar rensas efter ~10 dygn, men EV-laddarens
EGEN effektsensor (`sensor.0xf4ce365d8573ed2f_total_active_power`) har
permanent timstatistik — det är så dessa hittades, genom att skanna efter
timmar med `mean > 0`.

| Datum | Tid | Effekt | Natt? |
|---|---|---|---|
| 4 feb | 12:00–15:00 | 1,2–2,6 kW | Nej, dagtid |
| 15→16 feb | 22:00–00:00 | 2,4–2,6 kW, avtar | Ja (kväll/natt) |
| 21 feb | 00:00–02:00 | 2,56 → 0,45 kW | Ja |

EV-laddaren sitter på **fas 1** (bekräftat av användaren).

## 4. Komplett vinternatt med allt aktivt — 20/2→21/2

Den mest informationsrika natten hittills. Alla siffror stämmer inbördes
(huslast + batteri + EV summerar till uppmätt nätimport inom några watt):

| Kl | Huslast | Batteri | Nätimport | EV | Dämpad temp |
|---|---|---|---|---|---|
| 20:00 | 2873W | +2870 (urladdar) | 0,03 kW | — | -2,1°C |
| 21:00 | 2818W | +2704 (urladdar) | 0,34 kW | — | -2,1°C |
| 22:00 | 3053W | -1960 (laddar) | 7,64 kW | — | -2,0°C |
| 23:00 | 2316W | -3092 (laddar) | 8,05 kW | — | -1,8°C |
| 00:00 | 2466W | -2949 (laddar) | 8,03 kW | 2,56 kW | -1,7°C |
| 01:00 | 2246W | -5287 (laddar) | 8,00 kW | 0,45 kW | -1,5°C |
| 02:00 | 2179W | -5814 (laddar) | 8,06 kW | — | -1,1°C |
| 03:00 | 2493W | -5502 (laddar) | 8,04 kW | — | -0,7°C |
| 04:00 | 2717W | -2346 (laddar, avtar) | 5,11 kW | — | -0,2°C |
| 05:00 | 2768W | +844 (urladdar) | 1,94 kW | — | 0,3°C |

Tecken: `sensor...battery_inout` negativ = laddar, positiv = urladdar
(Sonnens råa konvention, SEM vänder den internt).

**VIKTIGT: den här natten körde användaren ett medvetet test där totaleffekten
begränsades till 8 kW**, för att utvärdera hantering av effekttariffer.
Platån 22:00–03:00 är alltså en artificiell testgräns, INTE naturligt
beteende — batteriets 8 kW egen maxgräns (`battery_max_power_kw`) nåddes
aldrig på riktigt, nättotalen hölls nere istället.

**Effekttariff-kravet är sedan dragits tillbaka — begränsningen kommer inte
att användas igen.** Samma 8kW-platå syns även natten 15/16 feb, så testet
pågick under en period, inte bara en enskild natt. Vi bedömer att det
**inte finns någon period i tillgänglig historik utan den här begränsningen
där EV + batteriladdning + kompressor konkurrerar naturligt** — måste
uppskattas från komponenterna istället (se avsnitt 6).

## 5. Fasfördelning

Bekräftat av användaren:

| Fas | Bär |
|---|---|
| **Fas 1** | Elpatron (halva) + **EV-laddare** + IT-andel + batteriandel |
| **Fas 2** | Kompressor (hel, ensam) + IT-andel + batteriandel |
| **Fas 3** | Elpatron (halva) + IT-andel + batteriandel |

Batteri-invertern är 3-fas och fördelar jämnt (redan dokumenterat i
`energy_controller.py`s docstring). IT-lasten (~830W sommartid) fördelas
"på alla faser" enligt användaren — antas jämnt (~277W/fas) i avsaknad av
mer specifik uppgift.

**Fas 1 är den enda fasen där EV-laddning och batteriets nattladdning direkt
konkurrerar om samma utrymme.** Fas 3 är i praktiken den minst belastade
fasen i de flesta scenarier (varken kompressor eller EV, bara halva
elpatronen + IT + batteri).

Grov efterhandsberäkning för 20/2-natten låg i intervallet 15,8–19,8A
beroende på exakta antaganden — nära men inte entydigt över den
`max_current_per_phase`-gräns (18A i konfigurationen) som skulle ha
utlöst `_apply_phase_limits()`. Inte tillräckligt tydligt för att dra
slutsatsen att fasskyddet (och inte bara det medvetna 8kW-testet) var
aktivt den natten — lämnat som öppen fråga, inte en bekräftad slutsats.

## 6. Uppskattning utan effektbegränsning (komponentbaserad, inte mätt)

Eftersom ingen obegränsad period med EV+batteri samtidigt hittats, byggs
detta ihop från de delar vi mätt separat var för sig:

**Vad reserven/golvet faktiskt behöver täcka** (batteriets EGEN laddning
räknas INTE hit — den fyller på reserven, den är inget som måste täckas):
- Baslast: ~830W
- Kompressor: ~1,5–1,7 kW vid ca -2°C (öppen fråga: hur skalar detta vid
  kallare temperaturer än vad vi sett i data hittills, t.ex. -15 till -20°C)
- EV, när den faktiskt laddar: ~2,5 kW, under laddningsfönstret (1–3h), inte
  hela natten
- Typisk "måste-täckas"-natt utan EV: ~2,3–2,5 kW snitt

**Vad hela huset kan dra samtidigt** (fas-/säkringsfråga, separat från golvet):
- Baslast + kompressor + EV + batteri vid sin egen 8kW-gräns ≈ **~13 kW**
  totalt i värsta fall, men fas 1 (elpatron + EV + batteriandel) blir
  troligen bindande innan totalen gör det.

## Sensor-referens (alla använda i den här analysen)

| Sensor | Typ | Användning |
|---|---|---|
| `sensor.el_forbruk_power_power` | measurement, W | Huslast (Elmätare 4, exkl. sol/batteri/EV) |
| `sensor.smart_energy_manager_house_load` | measurement, W | SEM:s egen (median-filtrerade) huslast — ingen historik före integrationen fanns |
| `sensor.sonnenbatterie_271100_state_battery_inout` | measurement, W | Batteri ladda/urladda, rå Sonnen-konvention |
| `sensor.elmatare_active_power_plus_q1_q4` | measurement, kW | Total nätimport (huvudmätare) |
| `sensor.thermostat_dampedoutdoortemp` | measurement, °C | Dämpad (utjämnad) utetemperatur — bättre proxy för värmebehov än momentan utetemp |
| `sensor.boiler_outdoortemp` | measurement, °C | Momentan utetemp (ej dämpad) |
| `sensor.0xf4ce365d8573ed2f_total_active_power` | measurement, kW | EV-laddarens egen effekt — permanent statistik, användes för att hitta laddningstillfällen trots rensad switch-historik |
| `sensor.boiler_nrgconstotal` m.fl. (se avsnitt 2) | total_increasing, kWh | Pannans energiuppdelning |
| `sensor.smart_energy_manager_yesterday_consumption_excl_ev` | — | Gårdagens faktiska förbrukning, exkl. EV — finns men används INTE i `build_plan()` idag |
| `sensor.smart_energy_manager_produktionskvot_3_dygn` | measurement | P3-2 produktionskvot, se `_update_pv_production_ratio()` i coordinator.py |

**Begränsning att komma ihåg:** rå state-historik (switchar, EV-status)
rensas efter ~10 dygn. Allt bortom det måste rekonstrueras via
long-term statistics (`source=statistics`) på sensorer med `state_class`
satt — fungerar bara för de sensorer som faktiskt har det.

## Öppna spår

1. **Ingen kodändring gjord än** för golvformeln — väntar på att vi är
   klara med underlaget. Kandidater: (a) skicka in verklig nattlast/
   `yesterday_consumption_kwh` till `build_plan()` istället för momentan
   `house_load_w`, (b) undersöka varför produktionskvotens historik verkar
   ha färre dygn än designen förutsätter.
2. Hitta eller uppskatta en period där elpatronen (rumsvärme eller
   varmvatten) faktiskt är aktiv, för att kvantifiera dess bidrag.
3. Skala kompressorns förbrukning mot KALLARE temperaturer än de -0,2
   till -4°C vi sett hittills (riktig vinter, -15°C+).
4. Bygga förbrukningsprognosen (piece 2 i den ursprungliga uppdelningen):
   sannolikt baserad på verklig COP (`nrgsupp*` / `nrgcons*`) mot dämpad
   utetemp, istället för dagens platta temperaturmodellskonstant.
5. Ta ställning till om fas 1:s EV/batteri-konkurrens ska in i
   planeringslogiken (t.ex. varna eller prioritera), eller lämnas som är.
6. P4-2 (absorptionstrappan, negativt pris) och P7-1/P7-2 (backtest) är
   avslutade och pushade (v0.7.5–v0.8.0) — separat spår, inte del av den
   här förbrukningsutredningen.
