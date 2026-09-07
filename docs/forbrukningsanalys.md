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

## 8. v0.9.0 live-testad — exportgolvets `can_export`-spärr verkar strukturellt svår att öppna på högförbrukningsdygn

Efter att golvformeln (avsnitt "Bakgrund" ovan) fixades och committades som
v0.9.0 (säsongsmedveten laddning/urladdning — se `docs/`-historiken/CHANGELOG)
testades den nya koden mot riktig live-data från HA (2026-09-07 kväll,
batteri 94% SOC, gårdagens förbrukning 28,76 kWh, morgondagens Nordpool-
priser redan publicerade med en kvällstopp på 152 öre — högre än dagens
egen kvällstopp). Planen gav `export=0 slots` trots det attraktiva priset.

**Orsak, verifierad med exakta (icke avrundade) mellanvärden:**

```
hourly_load_kw = 1,1983 kW   (från gårdagens 28,76 kWh — v0.9.0-fixen i drift)
golv (pv_kvot 0,86)          = 24,82 kWh
net_solar_tomorrow_kwh (p10) = 1,86 kWh
```

(Första körningen visade felaktigt `net_solar=0,0` — mitt eget testunderlag
saknade `pv_estimate10` i den syntetiska Solcast-JSON:en, vilket
`price_scheduler.py:230` tolkar som 0 rakt av. Rättat och omkört ovan.)

Testade vad som krävs för att stänga gapet (1,9 mot 24,8 kWh):

| Scenario | Resultat |
|---|---|
| pv_kvot höjs till ≥0,90 (minsta möjliga påslag) | golv → 21,1 kWh — fortfarande långt över 1,9 |
| Imorgondagens sol antas helt säker (p10=p50=19,57 kWh) | net_solar → 8,18 kWh — fortfarande under bästa golvet |
| Båda samtidigt (bästa tänkbara fall) | 8,18 mot 21,1 kWh — gapet krymper men stänger inte |

**Strukturell insikt (inte en ny bugg — `can_export`-villkoret i
`energy_planner.py` är orört den här sessionen):** `net_solar_tomorrow_kwh`
mäter dagens sol-ÖVERSKOTT (sol minus last, per kvart, bara positiva bidrag)
— i sig en liten siffra eftersom huslasten äter upp det mesta av dagens sol
innan något blir "netto". `export_floor_kwh` mäter hela NATTENS
reservbehov (15,5 timmar i det testade fallet) — stor nästan per
definition. Att kräva att det förra fullt ut ska täcka det senare är en
mycket sträng spärr. På ett dygn med förbrukning i den här
storleksordningen (28,76 kWh) verkar spärren strukturellt sett aldrig
kunna öppnas, oavsett hur attraktivt priset är eller hur mycket sol som
väntas — inte ens en garanterat solig dag räcker matematiskt i det här
exemplet.

**Öppen fråga, kräver ett medvetet beslut (inte gjort i den här
sessionen):** är den här strängheten avsiktlig (hellre för försiktig än
att riskera ett tomt batteri inför en dag med osäker sol), eller borde
`can_export`-villkoret jämföra mot en bråkdel av golvet istället för hela
golvet? Bör även testas mot fler verkliga dygn (lägre gårdagsförbrukning,
högre pv_kvot) för att se om gapet är lika stort generellt eller mest
akut på högförbrukningsdygn som det testade.

### Allvarligare upptäckt: golvet blockerar ÄVEN självkonsumtion, inte bara export

Körde ut hela 24h-planen (113 kvartar) för samma testfall och hittade något
värre än att export-spärren är sträng: **batteriet gör ingenting alls,
inte ens vanlig självkonsumtion, genom hela morgondagens kvällstopp.**

Planen 09-08 17:30–22:30+: `idle`, SOC fast på 98,1%, medan säljpriset
stiger till 1,58 kr/kWh vid 20:15 — batteriet står stilla bredvid ett
huspris på fullt spotpris. Detta trots att batteriet är nästan fullt.

**Orsak, spårad exakt:**
```
export_floor_kwh          = 24,4 kWh
användbart spann (20%→99% SOC) = 24,25 kWh   (0,79 × 30,69 kWh)
```
Golvet är STÖRRE än hela det spann batteriet får röra sig inom. Det gör
att `evening_target_soc_pct = min(99, 20 + golv/kapacitet×100)` klämmer
mot taket (99%) — kvällsmålet blir alltså identiskt med maxgränsen.
Samma golv-värde driver även den riktiga styrlogikens
`apply_plan_executor()`-check `self_consume_ok = battery_soc_pct >
evening_target` — som därmed aldrig kan bli sant, eftersom batteriet inte
kan gå över 99%. Golvet blockerar alltså både export OCH vanlig
självkonsumtion samtidigt, på exakt samma sätt, av exakt samma siffra.

**Rotorsak till varför golvet blir så stort:** `hourly_load_kw` (1,1983
kW, från gårdagens 28,76 kWh/24h) är ett DYGNSSNITT som appliceras platt
över hela det 15,5 timmar långa mörka fönstret. Men huset har en tydlig
dygnsrytm (avsnitt 1: sommarens nattbaslast var ~830 W, klart under
dygnssnittet) — dagtidsförbrukning (matlagning, apparater, ev. sol-styrd
last) drar upp dygnssnittet men förbrukas inte alls under natten. Att
använda dygnssnittet som natt-takt överskattar alltså natt-behovet
systematiskt, särskilt på långa mörka fönster och särskilt på dygn med
hög dagtidsförbrukning.

**Vad vi kan göra åt det — inte implementerat, väntar på beslut:**

1. **(Sannolikt störst effekt) Använd en natt-specifik lasttakt istället
   för dygnssnittet i golvformeln.** T.ex. en konfigurerbar
   natt-baslast (~830 W sommartid enligt avsnitt 1) eller en kvot mot
   dygnssnittet (natt ≈ 70% av dygnssnitt, grovt uppskattat), istället för
   att anta samma takt dygnet runt. Löser sannolikt merparten av
   problemet utan att röra osäkerhetspåslaget eller `can_export`-villkoret.
2. **Skyddsspärr:** klamra `export_floor_kwh` så den aldrig äter mer än
   t.ex. 85–90% av det användbara SOC-spannet (max_soc − min_soc),
   oavsett vad formeln annars räknar fram — garanterar alltid lite
   utrymme för självkonsumtion/export, som ett skyddsnät snarare än en fix
   på rotorsaken.
3. Kombinera 1+2 — natt-specifik takt som huvudfix, skyddsspärren som
   bälte-och-hängslen mot framtida extremfall.

Ingen av dessa är implementerad. Kräver användarens beslut om prioritet
och exakt utformning innan kod skrivs.

**Uppdatering: implementerad (v0.9.1), se nästa avsnitt för underlaget och
"Reviderad rekommendation" nedan för vad som faktiskt byggdes.**

### Natt-vs-dag grävt vidare — helårsdata visar att remedy 1 INTE räddar det observerade fallet

Innan ett beslut togs grävdes remedy 1 ("natt ≈ 70% av dygnssnittet")
vidare med riktig timstatistik (`sensor.el_forbruk_power_power`,
`source=statistics`, `period=hour`) för tre hela månader som representerar
olika säsonger: oktober 2025 (mellansäsong), januari 2026 (djupvinter) och
juli 2026 (sommar). Kvot = nattmedel (22–06 lokal tid) / 24h-medel,
snittat över alla kompletta dygn i respektive månad:

| Månad | Nattmedel | 24h-medel | Kvot natt/24h |
|---|---|---|---|
| Oktober 2025 (31 dygn) | 1167 W | 1229 W | **0,952** |
| Januari 2026 (31 dygn) | 2417 W | 2427 W | **0,994** |
| Juli 2026 (31 dygn) | 803 W | 1118 W | **0,726** |

**Det här var inte väntat:** natten är bara tydligt lägre än dygnssnittet
på **sommaren**. I januari är natt och dygn praktiskt taget identiska
(kvot 0,994) — kompressorn/elpatronen håller samma takt dygnet runt när
det är kallt, så det finns knappt någon "nattrabatt" att hämta. Oktober
ligger däremellan men fortfarande nära 1,0 (0,952). Bara sommarens avsnitt
1-siffra (~830 W natt mot ett betydligt högre dygnssnitt, kvot ≈0,73) är
den verkliga avvikelsen — inte normen.

**Konsekvens för det faktiska buggfallet (2026-09-07, `yesterday_consumption_kwh
= 28,76 kWh`):** ett dygn med så hög förbrukning är redan uppvärmningssäsong
till sin karaktär, inte en sommardag — dess natt/dygn-kvot ligger sannolikt
nära oktober- eller januarisiffran (~0,95–0,99), inte sommarens 0,73. Om
remedy 1 hade implementerats med en generell kvot skulle den bara sänkt
`hourly_load_kw` från 1198 W till ungefär **1141–1191 W** (räknat med
oktober- respektive januarikvoten) — en marginal på 1–5 %. Golvet (24,4 kWh)
låg **0,15 kWh över** det användbara spannet (24,25 kWh); en 1–5 %-sänkning
av natt-takten hade krympt golvet med uppskattningsvis 0,2–1,2 kWh (natt-
delen är bara en del av `behov_kwh`, och hela summan multipliceras
dessutom med osäkerhetspåslaget `_uncertainty_markup(pv_kvot)`, upp till
+300 % vid lågt `pv_production_ratio`). Det är i bästa fall precis i
underkant av vad som krävdes — och långt ifrån den marginal som behövs för
att inte råka i samma läge igen på ett ännu sämre dygn.

**Slutsats — remedy 1 ensam räddar INTE det rapporterade fallet.** Den ger
en verklig, mätbar förbättring men bara på sommardygn, vilket är precis
de dygn där golvet redan är litet och sällan är problemet. Det faktiska
felfallet inträffar på hög-förbrukningsdygn (höst/vinterkaraktär) där
natt ≈ dygn — där ger remedy 1 nästan ingenting, och det är
osäkerhetspåslaget (upp till 300 %) som dominerar golvets storlek, inte
dygnssnitt-approximationen.

**Reviderad rekommendation:** implementera remedy 2 (skyddsspärr,
`export_floor_kwh` klämd till t.ex. 85–90 % av användbart SOC-spann) som
den **primära, nödvändiga** fixen — den är säsongsoberoende och garanterar
alltid utrymme för självkonsumtion oavsett vad formeln räknar fram. Remedy 1
(natt-specifik takt) är fortfarande värd att lägga till som en sekundär
förbättring för att göra sommargolvet mer träffsäkert, men ska INTE
betraktas som lösningen på den här buggen — bara som en extra finjustering
som råkar sakna effekt just på det dygn som avslöjade problemet.

**Implementerat (v0.9.1):** `_FLOOR_SAFETY_CAP_FRACTION = 0.85` i
`energy_planner.py`, klämmer `export_floor_kwh` mot
`(batt_max_kwh − batt_min_kwh) × 0.85`. Verifierat mot exakt samma
live-rekonstruerade scenario som avslöjade buggen (09-07, 94% SOC,
`yesterday_consumption_kwh=28,76`): golvet föll från 24,4 → 20,6 kWh,
kvällsmålet från 99% (klämt mot taket) → 87%, och 24h-planen visar nu
verklig `cover_load`-urladdning genom hela kvällens och morgondagens
prisrörelser istället för `idle`. Remedy 1 (natt-specifik takt) inte
implementerad — bedömd som en framtida sekundär förbättring, inte en del
av den här fixen.

### Tillägg: 7-dygns rullande snitt istället för enda-dags gårdag (v0.9.1)

Uppföljande fråga från användaren ("varför är golvet fortfarande stort om
det ändå ska räcka 24h?") ledde till en kontroll av om gårdagens 28,76 kWh
var representativ. Verklig dygnsstatistik (`sensor.el_forbruk_power_power`,
20 aug–5 sep, 17 kompletta dygn) gav ett snitt på **1002 W (24,06 kWh/dygn)**
— gårdagen låg alltså ~20% över det normala, inte en ren engångshändelse
men tillräckligt för att ensam driva golvet mot taket (se tabell nedan).

Användaren bad om ett 7-dygns rullande snitt istället för `yesterday_consumption_kwh`,
med ett tillägg: **extra varmvatten som körs på solöverskott ska INTE räknas
med** (det är utöver normal förbrukning, drivet av dagens sol och säger
inget om nattens behov), medan **legionella-desinficeringen (var 7:e dag)
ska räknas med som vanligt** (verklig återkommande last, redan korrekt
representerad av att ett 7-dygnsfönster fångar exakt en cykel).

Verifierat mot verklig konfiguration: `heat_pump_extra_hot_water_entity`
(`switch.thermostat_dhw_chargethermostat_dhw_charge`, absorptionstrappans
steg 2) och `legionella_switch_entity` (`switch.boiler_dhw_disinfecting`)
är två helt separata switchar — går alltså att särskilja. Switch-historik
(9 dygn) visade extra varmvatten aktivt 5 av 9 dagar (11–80 min/dag),
legionella en gång (5 sep, ~60–90 min, matchar 7-dagarscykeln). Användaren
bekräftade att extra varmvatten körs UTAN samtidig rumsvärme — all effekt
på `heat_pump_power_entity` (`sensor.ivt_total_active_power`) under de
fönstren kan alltså tillskrivas varmvattnet fullt ut, ingen uppdelning
behövs.

**Effekt (samma testscenario):**

| Indata | `hourly_load` | Golv utan spärr | Golv med 85%-spärr |
|---|---|---|---|
| Gårdagen ensam (28,76 kWh) | 1198 W | 23,27 kWh (96%) | 20,61 kWh (87%) |
| 7-dagars rullande snitt (25,00 kWh) | 1042 W | **20,36 kWh (86%)** | 20,36 kWh (oförändrat — spärren behövs inte) |

Med det rullande snittet hamnar golvet redan UNDER 85%-spärrens tröskel —
spärren blir ett rent skyddsnät för dagar då även flerdagarssnittet slår
fel, istället för den enda saken som räddar dagen.

**Implementerat (v0.9.1):**
- `EnergyState.rolling_consumption_kwh` — nytt fält.
- `coordinator.py`: ny lagringsfil `{DOMAIN}_daily_consumption` (samma
  mönster som `_pv_ratio_store`), håller de 7 senaste dygnens
  `{date, total_kwh, extra_hw_kwh, net_kwh}`. Ackumulerar extra
  varmvatten-energi löpande (`heat_pump_power_w × dt` medan switchen är
  på, 30 min tak per pollning mot omstartshopp), nollställer och rullar in
  gårdagens nettosiffra vid dygnsskifte (samma "ny dag detekterad"-mönster
  som `_temp_sample_date`/`_pv_ratio_date`).
- `energy_planner.py::build_plan()`: ny parameter `rolling_consumption_kwh`,
  prioriteras i `_eff_daily_kwh = max(predicted_daily_kwh, rolling_consumption_kwh
  or yesterday_consumption_kwh or 0.0)` — faller tillbaka till gårdagen
  ensam tills 7 dygns historik hunnit byggas upp efter driftsättning.
- Verifierat direkt mot `build_plan()`: samma scenario med
  `rolling_consumption_kwh=25.0` istället för bara `yesterday_consumption_kwh=28.76`
  ger golv 20,02 kWh (85%) — bekräftar att prioriteringen fungerar och att
  spärren (v0.9.1, tidigare i det här avsnittet) och det rullande snittet
  kompletterar varandra som tänkt.

### Prisstyrd golvavlämpning ("Option B") + P3-2-buggen — natt-fönstret grävt klart (v0.9.1)

Uppföljande diskussion: användaren observerade att natt-fönstret (22:15–04:30,
köppris ~1,27–1,33 kr) fortfarande visas som "idle: batteri vid golvet, nät
täcker" trots att batteriets egen lagrade energi (snittkostnad 0,74 kr/kWh
+ 0,05 kr cykelkostnad = 0,79 kr) är billigare än nätpriset — en rimlig
invändning: varför köpa dyrare el när billigare redan finns i batteriet?

**Tre varianter testade mot samma scenario, innan kod skrevs:**

1. **Naiv prisspärr ("Option B"):** öppna golvet mot `hard_floor_kwh`
   närhelst `köppris > batterikostnad + cykelkostnad`. Eftersom nätavgift
   + skatt + moms lägger ett golv på ~1,2–1,3 kr/kWh även vid nära-noll
   spotpris, är villkoret **i praktiken alltid sant** — testet visade att
   batteriet skulle laddas ur hela natten och landa på 52,5% SOC redan kl
   08:15, långt under det tänkta 85%-målet.
2. **Variant A (relativ, 75:e percentilen av kvällens/nattens egna
   priser):** självjusterande, öppnar bara morgonrampen (07:15–08:15).
   Robust mot både batterikostnads-reset (`reset_battery_cost`-tjänsten)
   och en generell prisnivåförskjutning (testat genom att skifta hela
   dygnets pris med en konstant offset) — tröskeln flyttar sig med.
3. **Variant C (fast marginal, batterikostnad+cykel+0,60 kr):** samma
   ungefärliga effekt som A i det ORIGINALA scenariot, men **bevisat
   sårbar**: en `reset_battery_cost` (nollställer batterikostnaden) sänker
   tröskeln till 0,65 kr och återskapar den naiva dränering-buggen; en
   generell prisnivåhöjning (testat: natt-lägsta 0,20 kr istället för
   verkliga ~0,03 kr) gör samma sak eftersom marginalen är ett fast tal,
   inte relativt till dygnets egna priser.

**"0 sol imorgon"-konsekvensanalys (naiv B):** vid batteriet 52,5% kl 08:15
och verkligt noll sol hela vägen skulle batteriet nå `min_soc` kl **17:49**
— cirka 2 timmar INNAN nästa kvällspeak (20:00, 3,13 kr/kWh). Den nuvarande
lösningen (utan prisspärr) räcker till 02:44 nästa natt med bred marginal.
En garanterad liten besparing (~2 kr/natt) mot en verklig risk att missa
exakt det golvet ska skydda mot.

**Är "0 sol imorgon" ett realistiskt värsta fall? — bekräftat med hela årets
riktiga data.** `sensor.sg_daily_pv_generation`s HA-statistik gick bara
tillbaka till 5 aug 2026 (troligen en omstart av Sungrow-integrationen då),
så den frågan gick inte att besvara mot HA. Användaren exporterade istället
ett helt års dygnsdata direkt från växelriktarens egen molntjänst (Sungrow
iSolarCloud, "Monthly report"-export) — 12 CSV-filer,
`testdata/Monthly report_028778 - Fredrik Jonson_*.csv`, **2025-09-01 till
2026-08-31, alla 365 dygn utan luckor.**

Resultatet var tydligare och allvarligare än skärmdumparna antydde:

| | Antal |
|---|---|
| Dygn med exakt 0,0 kWh | **15** |
| Dygn under 1,0 kWh | 27 |
| Längsta sammanhängande nollperiod | **11 dygn i rad** |

De 15 nolldygnen: 2025-11-20, 2025-12-11, **2026-01-04 till 2026-01-14
(elva dygn i sträck, exakt 0,0 kWh varje dygn)**, 2026-02-07, 2026-02-11.
Ingen annan månad hade mer än ett enstaka isolerat nolldygn — januariperioden
är unik i datan, sannolikt snötäckta paneler som satt kvar under en hel
köldknäpp.

**Slutsats, reviderad:** en isolerad engångs-nolldag är fortfarande sällsynt
(bara 2 av 15 nolldygn var isolerade — de flesta klustrar). Men
elva-dygns-perioden är inte hypotetisk, den hände verkligen, och den är
STRÄNGARE än det "0 sol imorgon"-test som användes för att döma ut den naiva
Option B ovan (som bara testade ETT dygns bortfall). Ingen golvformel som
planerar "till nästa förväntade soltakeover" kan överleva elva dygn i rad —
det är inte en kalibreringsfråga, det är fysiskt omöjligt oavsett hur stor
reserv som byggs in. `_uncertainty_markup()`s maxpåslag (golvet mättas mot
`batt_max_kwh`, alltså full nattautonomi) skjuter i bästa fall problemet
en eller ett par dygn framåt, inte elva.

**P3-2-bugg hittad under den här grävningen:** `_update_pv_production_ratio()`
(coordinator.py) committade bara ett dygn till kvot-historiken om
`_pv_last_actual_reading > 0` — men `_get_state_float()` returnerar `0.0`
BÅDE när sensorn är otillgänglig OCH när den korrekt läser av en genuin
nolla. Ett riktigt nollproduktionsdygn (som januaris snöperiod) skulle
alltså ha hoppats över HELT från historiken, inte räknats som en dålig
kvot — mekanismen som ska upptäcka precis det scenariot var blind för det.
Verifierat med en fristående repro: kvoten frös på 0,88 (från soliga dagar
innan snön) genom hela den simulerade snöperioden med den gamla koden,
föll korrekt till 0,00 med fixen.

**Implementerat (v0.9.1):**
- **P3-2-fixen:** ny `self._pv_last_actual_available`-flagga (state-nivå,
  skild från det numeriska värdet) avgör om ett dygn ska committas till
  `_pv_ratio_history` — en genuin nolla räknas nu in korrekt.
- **Option B (naiv variant), som uttrycklig INTERIMSLÖSNING:** implementerad
  i `build_plan()`s mörka-slot-logik (`_PRICE_GATE_PV_CONFIDENCE = 0.8`),
  markerad i koden med en kommentar om dess kända svaghet. Användaren bad
  uttryckligen om att lägga till B "medan vi funderar på hur den riktiga
  lösningen ska se ut" — Variant A (självjusterande percentil) är fortfarande
  den bedömt säkrare lösningen men INTE implementerad än.
- **Kvarstår, inget beslutat:** (a) byta ut naiva B mot Variant A eller en
  annan säkrare variant, (b) hur golvet ska hantera en flerdagars
  nollproduktionsperiod (inte bara "till nästa dag") — inget av detta är
  löst, bara identifierat.

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
| `testdata/Monthly report_028778 - Fredrik Jonson_*.csv` | — | **Inte en HA-sensor.** Dygnsvis PV/köp/export/last-export direkt från växelriktarens molntjänst (Sungrow iSolarCloud), 12 filer = helt år utan luckor (2025-09-01–2026-08-31). Källan till den bekräftade elva-dygns-nollperioden (avsnitt 8) — HA:s egen `sensor.sg_daily_pv_generation`-statistik täcker bara från 5 aug 2026 och kunde inte användas för det. |

**Begränsning att komma ihåg:** rå state-historik (switchar, EV-status)
rensas efter ~10 dygn. Allt bortom det måste rekonstrueras via
long-term statistics (`source=statistics`) på sensorer med `state_class`
satt — fungerar bara för de sensorer som faktiskt har det.

## Öppna spår

1. **Löst och driftsatt (v0.9.0):** golvformeln skickar nu in
   `yesterday_consumption_kwh`/`house_load_avg_w` till `build_plan()`
   istället för momentan `house_load_w` (samma mönster som v0.7.6-fixen i
   `_auto_mode()`). Committat i två separata commits (P4-2 v0.8.0 följt av
   säsongsmedveten v0.9.0 — de låg oavsiktligt blandade i samma
   arbetskopia och delades upp hunk-för-hunk). **Löst (v0.9.1, avsnitt
   "Prisstyrd golvavlämpning" ovan):** anledningen till att
   produktionskvotens historik hade färre dygn än designen förutsatte var
   en verklig bugg — `_update_pv_production_ratio()` hoppade över varje
   genuint nollproduktionsdygn istället för att räkna in det, eftersom den
   inte kunde skilja "sensor otillgänglig" från "verklig nolla". Fixat.
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
4. **Delvis löst (v0.9.0):** temperaturmodellen är omkalibrerad mot
   helårsdatan (avsnitt 7) och stödjer nu `damped_outdoor_temp_entity`.
   Kvarstår: en riktig COP-baserad modell (`nrgsupp*` / `nrgcons*`) mot
   dämpad utetemp — nuvarande modell är fortfarande en enkel gradday-
   formel, bara med bättre kalibrerade konstanter.
5. **Delvis adresserat (v0.9.0):** EV är nu med i planeringslogiken —
   reservmarginal i golvet (`ev_reserve_margin_kwh`) och billigast-timmar-
   laddning i `_auto_mode()`. Kvarstår: fas 1:s EV/batteri-konkurrens
   (avsnitt 5/6 ovan) hanteras fortfarande bara reaktivt av
   `_apply_phase_limits()`, inte proaktivt i planeringen.
6. P4-2 (absorptionstrappan, negativt pris) och P7-1/P7-2 (backtest) är
   avslutade och pushade (v0.7.5–v0.8.0) — separat spår, inte del av den
   här förbrukningsutredningen.
7. **Nytt, allvarligare än först trott, och grävt klart (avsnitt 8):**
   golvet (`export_floor_kwh`) kan bli STÖRRE än hela det användbara
   SOC-spannet (max_soc−min_soc), vilket klämmer `evening_target_soc_pct`
   mot taket och blockerar BÅDE export OCH vanlig självkonsumtion —
   batteriet kan stå stilla genom en hel pristopp trots att det är nästan
   fullt. Helårsdata (okt/jan/jul, natt-vs-dygn-kvot) visade att den
   ursprungligen mest lovande fixen (natt-specifik lasttakt) bara hjälper
   på **sommardygn** (kvot 0,73) — på hög-förbrukningsdygn av
   höst/vinterkaraktär (det faktiska buggfallet, kvot ~0,95–0,99) är natt
   ≈ dygn, så den fixen ensam hade INTE räddat det rapporterade fallet.
   **Löst (v0.9.1):** skyddsspärren implementerad — `export_floor_kwh`
   klämt till max 85% av användbart SOC-spann (`_FLOOR_SAFETY_CAP_FRACTION`
   i `energy_planner.py`). Verifierat mot buggfallets exakta scenario:
   golv 24,4→20,6 kWh, kvällsmål 99%→87%, batteriet gör nu verklig
   urladdning genom kvällens prispeak istället för att stå still.
   Kvarstår: natt-specifik lasttakt (sekundär förbättring för sommarens
   träffsäkerhet) — inte implementerad, inte prioriterad ännu.
8. **Nytt (v0.9.1, avsnitt "Prisstyrd golvavlämpning"):** golvet är en ren
   energitröskel — den väger aldrig köppris mot batteriets egen sparade
   kostnad. En naiv prisspärr ("Option B") är implementerad som uttrycklig
   INTERIMSLÖSNING på användarens begäran, med en känd svaghet (kan tömma
   nästan hela reserven på en natt om solprognosen slår fel — se avsnittet
   för "0 sol imorgon"-analysen). En säkrare självjusterande variant
   (percentiltröskel mot dygnets egna priser, döpt "Variant A" i analysen)
   är skisserad och testad men INTE implementerad. Under samma grävning
   hittades och fixades en verklig P3-2-bugg (se punkt 1 ovan) som gjorde
   att produktionskvoten aldrig upptäckte genuina nollproduktionsdygn.
   Kvarstår: (a) byt ut Option B mot Variant A eller bättre, (b) hantera
   flerdagars nollproduktionsperioder — **bekräftat med hela årets riktiga
   data** (`testdata/Monthly report_028778*.csv`, 365 dygn utan luckor):
   15 dygn med exakt 0,0 kWh, varav **11 dygn i rad (4–14 jan 2026)**.
   Golvet planerar idag bara fram till nästa förväntade soltakeover — ingen
   reservstorlek löser ett elva dygn långt bortfall, det kräver en helt
   annan strategi (acceptera nätberoende under perioden snarare än att
   jaga ett ouppnåeligt golv). Inte påbörjat.
