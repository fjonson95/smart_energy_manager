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

**Öppen fråga, delvis besvarad i avsnitt 7:** elpatronen visade sig vara
aktiv betydligt oftare än den enda januari-observationen antydde (11 dygn
på 5 veckor), men fortfarande i små doser (1–11 kWh/dygn) — se klassificeringen
i avsnitt 7 för varmvatten- (desinfektion) resp. rumsvärme-fallen (kallras).

## 3. EV-laddningsmönster — 5 hittade tillfällen på 5 veckor

Rå på/av-historik för laddare/switchar rensas efter ~10 dygn, men EV-laddarens
EGEN effektsensor (`sensor.0xf4ce365d8573ed2f_total_active_power`) har
permanent timstatistik — det är så dessa hittades, genom att skanna efter
timmar med `mean > 0`.

| Datum | Tid | Effekt | Natt? |
|---|---|---|---|
| 4 feb | 12:00–15:00 | 1,2–2,6 kW | Nej, dagtid |
| 15→16 feb | 22:00–00:00 | 2,4–2,6 kW, avtar | Ja (kväll/natt) — sammanfaller med elpatron-dygnet 15 feb |
| 20 feb | 19:00–20:00 | ~2,6 kW | Ja (tidig kväll) |
| 20→21 feb | 00:00–02:00 | 2,56 → 0,45 kW | Ja (samma natt som avsnitt 4) |

Fyra kalenderdygn med EV-laddning över 5 veckor (25 jan–28 feb), inget
tydligt regelbundet mönster (varken veckodag eller temperaturberoende).
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

## 7. Helårsbild (okt 2025–sep 2026) — elpatron-varmvatten följer solsäsongen, inte ett fast intervall

**Rättelse av tidigare antagande i det här dokumentet.** Statistiken går längre
tillbaka än vad vi trodde ("data ser ut att finnas efter slutet på januari"
stämde inte) — `boiler_nrgconstotal`, `boiler_auxelecheatnrgconstotal`,
`boiler_dhw_auxelecheatnrgcons` och `thermostat_dampedoutdoortemp` finns som
permanent dygnsstatistik hela vägen från **2025-10-01**, 340 kompletta dygn.

Med hela året synligt håller INTE "DHW-elpatron = periodisk desinfektion var
4–5:e vecka" (den slutsatsen byggde på bara 2 händelser i ett 5-veckors
fönster som råkade se jämnt fördelade ut). Verkligt mönster, dygn med
`dhw_auxelecheatnrgcons`-rörelse:

| Period | Dygn med elpatron-varmvatten | Mönster |
|---|---|---|
| okt–dec 2025 | 9 av ~90 dygn | var 1–3:e vecka, oregelbundet |
| 24 dec → 27 jan | **0** | 34 dygns sammanhängande lucka |
| feb–mars | stigande | var 3–10:e dygn |
| jun–sep | 60+ av ~100 dygn | **nästan varje dygn**, 4–9 kWh |

34-dagarsluckan i dec/jan motsäger ett strikt periodiskt schema. Kontrollerat
mot koden: `DEFAULT_LEGIONELLA_INTERVAL_DAYS = 7` (`const.py:123`) med
tvångskörning vid 1,5× intervallet (~10,5 dygn, `legionella.py:186`) — ett
riktigt legionella-schema med den gaten borde inte kunna hoppa över 34 dygn
oavsett pris/sol-läge.

**Förklaringen (från användaren):** signalen är en sammanslagning av tre
olika källor som alla värmer extra varmvatten och alla syns som samma
`dhw_auxelecheatnrgcons`-rörelse i statistiken:
1. Pannans egen inbyggda styrning (ems-esp-firmware, oberoende av SEM)
2. Manuella körningar (t.ex. testkörningar)
3. En Home Assistant-automation (troligen sol-överskott → extra varmvatten,
   i linje med projektmål 2, "maximera egenförbrukning")

Sökning i HA efter automationer/skript på "varmvatten" och "boiler" gav noll
träffar (`ha_search`, fritext + config-body-sök) — automationen (om den
fortfarande finns aktiv) använder sannolikt andra ord/entiteter i sin
konfiguration, eller ligger helt i pannans egen firmware och syns aldrig som
en HA-automation.

**Konsekvens för avsnitt 2 ovan:** raden "elpatronen för varmvatten var i
praktiken inaktiv" gällde bara för 18–31 januari — den perioden ligger mitt
i den 34-dagars vinterluckan och är INTE representativ för året. Sommaren
visar tvärtom nästan daglig aktivitet.

**Öppen fråga, kräver mer än energiräknarna för att lösas:** (a) namnet på
HA-automationen (om den finns) för att korsköra dess logbook/trigger-historik
mot dessa dygn, eller (b) tidsstämplar för manuella körningar, för att kunna
subtrahera dem och isolera den faktiska legionella-frekvensen.

### Kompressor vs temperatur — hela året, mycket tydligare samband

Kompressor-andelen (total minus elpatron) mot dämpad temp över hela perioden
ger ett betydligt tydligare, i stort sett monotont samband än den smala
vinterskivan (-0,2 till -9,9°C) som fanns tillgänglig tidigare:

| Dämpad temp | Kompressor, ungefärligt intervall (kWh/dygn) |
|---|---|
| +20 till +25°C (jul) | 2–8 |
| +10 till +20°C (maj, jun, sep) | 5–12 |
| 0 till +10°C (okt, mars–apr) | 10–20 |
| -5 till 0°C (nov–dec, feb–mar) | 20–45 |
| -10 till -5°C (jan–feb, kallast uppmätt: -13,2°C) | 40–65 |

Fortfarande inte perfekt linjärt — t.ex. gav 10/1 (-7,8°C) 76 kWh medan
11/1 (-13,2°C, kallare) bara gav 63 kWh, och 12/1 (-9,8°C) föll till 40 kWh.
Andra faktorer (vind, faktisk vattenförbrukning, ev. avfrostningscykler)
påverkar uppenbarligen minst lika mycket som momentan temperatur för ett
enskilt dygn — men riktningen och storleksordningen över säsongen är
entydig, till skillnad från den tidigare smala januari–februari-skivan.
Vind som möjlig delförklaring undersöks nedan.

Rådata: `full_year.csv` (scratchpad, ej incheckad — kan återskapas med
`ha_get_history(source="statistics", period="day")` på de fyra sensorerna
ovan från 2025-10-01 och framåt).

### Vind som förklaringsvariabel — lovande men inte en ren regel

Det finns faktiskt vindsensorer med permanent dygnsstatistik:
`sensor.unknown_70_ee_50_84_24_fc_regn_vindhastighet` (medelvind, statistik
från ~2025-12-03) och `..._vindbya_styrka` (byvind, statistik från
~2025-12-21). `..._vindriktning` (vindriktning) saknar `state_class` helt —
ingen statistik alls där.

Februari (samma "heat-only"-elpatrondygn som i huvudtabellen ovan) mot vind:

| Datum | Temp | Boiler | AuxD | Vind (medel) | Byvind (medel) | Byvind (max) | Elpatron? |
|---|---|---|---|---|---|---|---|
| 1–6 feb | -3,1 till -5,9°C | 38–48 | 0 | 1,3–1,8 | 3,3–4,7 | 6,9–10,0 | Nej |
| 8 feb | -5,0°C | 61 | 9 | 1,03 | 2,56 | 5,3 | Ja |
| 9 feb | -7,3°C (kallare!) | 42 | 0 | 0,47 | 1,08 | 2,5 | **Nej** |
| 14 feb | -9,9°C | 59 | 11 | **0,55** | **1,18** | 2,5 | Ja |
| 18 feb | -7,6°C | 59 | 4 | **0,41** | **1,01** | 2,5 | Ja |
| 19 feb | -8,4°C | 54 | 5 | **0,44** | **0,98** | 1,9 | Ja |

Flera av de STÖRSTA elpatron-dygnen (14, 18, 19 feb) inträffar vid ovanligt
**låg** vind (medel 0,4–0,55 m/s, bland de lugnaste dygnen hela månaden) —
motsatsen till "mer vind → mer värmeförlust → mer elpatron". Det stämmer
istället med ett känt beteende hos luft/vatten-värmepumpar: **kallt + fuktigt
+ vindstilla ger mest rimfrost på utedelens kylare**, vilket kräver fler/
längre avfrostningscykler — och många system slår till elpatronen som
tillfälligt stöd under avfrostning.

**Men det är ingen ren regel** — 9 februari är nästan lika vindstilla
(0,47 m/s) och kallare än 8:e (-7,3 vs -5,0°C), men fick INGEN elpatron alls,
medan 8:e (varmare, lite mer vind) fick 9 kWh. Vind förklarar alltså inte
allt själv heller.

**Rättelse:** `binary_sensor.ceed_defrost` är BILENS (Kia Ceed) vindrute-/
klimatavfrostning — helt orelaterat till huset/värmepumpen. Fel spår, hittat
via namnmatchning utan att kontrollera vad det faktiskt är.

**Rätt spår, verifierat:** helpern "Avfrostningsfunktion aktiv" är en
`template`-`binary_sensor` med källkoden
`{{ 'defrost' in states('sensor.boiler_hpactivity') | lower }}` —
`sensor.boiler_hpactivity` är pannans (ems-esp) egen aktivitetsstatus-sensor
(textvärden). Det HÄR är den riktiga avfrostningssignalen.

**Men samma retentionsproblem som `ceed_defrost`:** `sensor.boiler_hpactivity`
saknar `state_class` (det är en textsensor, inte numerisk — kan inte få
permanent dygnsstatistik ens i teorin). Kontrollerat direkt: en förfrågan om
60 dygns historik gav bara data från de senaste ~10 dygnen tillbaka (`off`,
`heating`, `hot water`, `pool`, `unavailable` — inget `defrost` denna period,
väntat i milt väder). **Går alltså inte att kontrollera mot förra vinterns
dygn i efterhand**, av samma skäl som `ceed_defrost`.

**Rätt åtgärd framåt (inte `state_class` — det gäller bara numeriska
sensorer):** antingen (a) förlänga recorder-retention specifikt för
`sensor.boiler_hpactivity` (`purge_keep_days`/`include` i recorder-config),
eller (b) bygga en numerisk räknare (t.ex. "avfrostningsminuter idag",
`total_increasing`) som KAN få permanent statistik, och som SEM eller en
HA-automation matar från just detta tillstånd. Utan endera går hypotesen
bara att verifiera live, dygn för dygn, framöver.

### Luftfuktighet testad också — motbevisar den enkla versionen av hypotesen

`sensor.utomhus_humidity` finns med permanent dygnsstatistik (verifierat,
data från åtminstone dec 2025). Lade till den i februari-tabellen för att se
om fukt + vind + temp tillsammans förklarar elpatron-dygnen bättre:

**9 feb vs 18 feb är ett nästan perfekt naturligt kontrollpar:**

| Datum | Temp | Vind (medel) | Luftfuktighet | Elpatron |
|---|---|---|---|---|
| 9 feb | -7,3°C | 0,47 m/s | 82,1% | **0 kWh** |
| 18 feb | -7,6°C | 0,41 m/s | 74,0% | **4 kWh** |

Nästan identisk temp, nästan identisk (ovanligt låg) vind — och 9:e var
t.o.m. FUKTIGARE än 18:e. Om "kallt + fuktigt + vindstillt → rimfrost →
avfrostning → elpatron" stämde borde 9:e ha varit MINST lika benäget att
utlösa elpatron som 18:e. Det blev tvärtom.

**Slutsats:** den enkla vädermodellen (temp+vind+fukt vid dygnsupplösning)
förklarar INTE vilka dygn som får elpatron. Antingen (a) är det något annat
som styr — vattenförbrukning, tidigare dygns islagerhistorik på kylflänsen,
en helt orelaterad orsak (manuell körning, automation) — eller (b) dygnsmedel
är fel upplösning: enskilda kalla/fuktiga/vindstilla TIMMAR kan ha funnits
även 9:e feb utan att synas i dygnsmedlet. Går inte att skilja åt utan den
riktiga `sensor.boiler_hpactivity`-signalen i högre tidsupplösning, vilket
som sagt inte finns kvar i historiken för februari.

Övriga sensorer kollade men inte hittade: solinstrålning/irradians (finns
inte som egen sensor, bara Solcast-prognoser), snösensor (finns inte —
snö-på-panelerna-effekten går bara att se indirekt via bortfall i faktisk
solproduktion mot Solcast-prognos). Nederbördssensor finns
(`..._nederbord_i_dag`, permanent statistik) men inte testad ännu.

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
| `sensor.unknown_70_ee_50_84_24_fc_regn_vindhastighet` | measurement, m/s | Medelvind — permanent statistik från ~2025-12-03 |
| `sensor.unknown_70_ee_50_84_24_fc_regn_vindbya_styrka` | measurement, m/s | Byvind — permanent statistik från ~2025-12-21 |
| `sensor.unknown_70_ee_50_84_24_fc_regn_vindriktning` | measurement, ° | Vindriktning — INGEN `state_class`, ingen statistik alls |
| `sensor.boiler_hpactivity` | text/enum | Pannans aktivitetsstatus (`off`/`heating`/`hot water`/`pool`/`defrost` m.fl.) — källa för helpern "Avfrostningsfunktion aktiv"; ~10 dygns retention, ingen `state_class` möjlig (textsensor) |
| `sensor.utomhus_humidity` | measurement, % | Utomhus luftfuktighet — permanent statistik, testad mot elpatron-dygn (avsnitt 7), motbevisade den enkla väderhypotesen |
| `sensor.unknown_70_ee_50_84_24_fc_unknown_05_00_00_0c_b4_54_nederbord_i_dag` | total (dygnsvis), mm | Nederbörd idag — permanent statistik, inte testad ännu |

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
2. **Delvis löst (avsnitt 7):** elpatron-varmvatten är INTE en periodisk
   desinfektionscykel — det är en blandning av pannans egen firmware,
   manuella körningar och en misstänkt HA-automation för sol-överskott.
   Kvarstår: hitta automationens namn (om den finns) för att korsköra mot
   logbook och isolera de tre källorna från varandra.
3. **Delvis löst, hypotesen om väder→avfrostning MOTBEVISAD på dygnsnivå
   (avsnitt 7):** kompressorns förbrukning mot temp är kartlagd över +25°C
   till -13,2°C — riktningen är tydlig men inte perfekt linjärt dygn-för-dygn.
   Testade vind OCH luftfuktighet (båda finns som permanent dygnsstatistik)
   som möjlig förklaring till vilka kalla dygn som får elpatron-aktivitet —
   9 feb och 18 feb är ett nästan identiskt par (samma temp, samma låga
   vind, 9:e t.o.m. fuktigare) men med helt olika utfall (0 resp. 4 kWh
   elpatron). Dygnsupplösning räcker alltså inte. Den riktiga
   avfrostningssignalen är hittad (`sensor.boiler_hpactivity` via helpern
   "Avfrostningsfunktion aktiv") men saknar långsiktig historik (textsensor,
   ~10 dygns retention, ingen `state_class` möjlig). Kräver antingen längre
   recorder-retention för just den sensorn, eller en ny numerisk räknare
   ("avfrostningsminuter/dygn") — annars går det bara att verifiera live,
   dygn för dygn, framöver.
4. Bygga förbrukningsprognosen (piece 2 i den ursprungliga uppdelningen):
   sannolikt baserad på verklig COP (`nrgsupp*` / `nrgcons*`) mot dämpad
   utetemp, istället för dagens platta temperaturmodellskonstant.
5. Ta ställning till om fas 1:s EV/batteri-konkurrens ska in i
   planeringslogiken (t.ex. varna eller prioritera), eller lämnas som är.
6. P4-2 (absorptionstrappan, negativt pris) och P7-1/P7-2 (backtest) är
   avslutade och pushade (v0.7.5–v0.8.0) — separat spår, inte del av den
   här förbrukningsutredningen.
