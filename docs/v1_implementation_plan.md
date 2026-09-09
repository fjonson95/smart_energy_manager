# Implementationsplan · v0.9.1 → v1.0

## Vägen till värdemodellen

Nio steg från koden som kör idag till en regulator där ett enda tal styr allt.
Varje steg lämnar systemet körbart, och de fyra första kan göras utan att
röra arkitekturen.

Bygger på granskningen av v0.5.60, förbrukningsanalysen
(`docs/forbrukningsanalys.md`), regelmodellsutredningen och läsning av
`energy_planner.py`, `energy_controller.py` och `coordinator.py` i v0.9.1.

**Status:** Steg 0 implementerat och verifierat 2026-09-08 (v0.9.2) — se
`docs/forbrukningsanalys.md` avsnitt "Steg 0 implementerat" för detaljer,
kodverifiering och acceptanstestresultat. **Steg 1 klart (v0.9.3–v0.9.6):**
`eta_roundtrip` och `sell_extra_revenue` uppdaterade till uppmätta värden,
brytpunktsformlerna verifierade (se korrigeringen om merit-order-viktat
snitt kontra dygnets min/max, nedan), `sem_battery_equivalent_cycles`
(v0.9.4), dygnsbudgetens dump-exkludering (steg 1B, v0.9.5) och
lastprofilen form×nivå (steg 1A, v0.9.6) alla implementerade och
verifierade. **Steg 2 klart (v0.9.7):** golvet är nu en avtagande
`reserve_at(t)`-bana istället för ett skalärt tal, skyddsspärren
(`_FLOOR_SAFETY_CAP_FRACTION`) borttagen, prisspärren ("Option B") behållen
i väntan på en riktig vinterbacktest — se avsnitt "Steg 2 implementerat"
nedan för detaljer och verifieringsresultat. **Steg 3 försökt men INTE
klart (v0.9.8–v0.9.11) — sex fynd (fem i planeraren, ett i
`testdata/backtest.py` självt: en trasig prissträng→float-konvertering
som gett alla tidigare procenttal i den här filen/CHANGELOG en missvisande
referens), fortfarande under steg 2 men gapet nu litet: −102,87 kr mot
steg 2:s −114,85 kr över samma 10 dagar (~90 % av steg 2:s vinst), INTE
driftsatt.** `energy_planner.py` innehåller för närvarande steg 3:s kod
(`reserve_at(t)` är borttagen ur filen, ersatt), men v0.9.7
(commit `a57cea2`) är den senast verifierade, säkra versionen att köra —
se avsnitt "Steg 3 implementerat" nedan, särskilt "Femte" och "Sjätte
fyndet", för
grundorsaksanalysen innan arbetet återupptas. **Steg 4 pausat** tills
steg 3 slår steg 2 i backtest — se motivering i "Steg 3 implementerat".
Steg 5–8 inte påbörjade.

---

### Utgångsläget är bättre än granskningen beskrev

Sedan den skrevs är NameError-buggen borta, fasskyddet testar `abs(phase_current)`
mot en marginal, vinterläget är avvecklat, skrivningarna har dödband,
huslastgolvet är villkorat på att sensorn saknas, `async_zero_battery()`
finns, strypningen mot Sungrow är inkopplad, och `apply_plan_executor` är
uttryckligen enda skrivstället — delat mellan drift och backtest.

Det som återstår är alltså inte en uppröjning. Det är ett byte av
beslutsprincip, plus en rad som råkade överleva den förra städningen.

---

## STEG 0 — Ta bort executorns veto
*en kväll*

Det observerade felet — batteriet står stilla genom pristoppen — har en
enda orsak, och den är en rad. Gör det här först och mät effekten innan
något annat rörs.

**Nu:** Planeraren beslutar `cover_load` via prisspärren. Executorn räknar
om `self_consume_ok` mot kvällsmålet, som härleds ur golvet, och lägger in
sitt veto. Vid SOC 59 % mot kvällsmål 73,7 % blir urladdningen 0 W.

**Mål:** Executorn klämmer mot fysiken — min_soc, effekt, faser. Policyn
ligger i planeraren, som redan vägt golv, pris och bana mot varandra.

1. **Ta bort kvällsmålet ur executorns grind.** `energy_controller.py` ·
   `apply_plan_executor`
   ```python
   self_consume_ok = (
       state.battery_soc_pct > self.battery_min_soc
       and state.battery_soc_pct > evening_target
   )
   ```
2. **Låt "mörk" betyda att solen inte täcker huset.** `energy_planner.py`
   Ersätt `_DARK_SOLAR_KW = 2.0` med en jämförelse per slot:
   `is_dark = slot.solar_kw < hourly_load_kw`. Tröskeln på 2 kW klassar en
   stor del av mellansäsongens och vinterns dagsljustimmar som natt.
3. **Låt prognososäkerhet höja golvet, inte stänga prisspärren.**
   `energy_planner.py` Villkoret `pv_production_ratio >= 0.8` stänger
   spärren vid snötäckta paneler — exakt de dygn reserven finns för. Ta
   bort det från spärren; osäkerheten hanteras redan av
   `_uncertainty_markup`.
4. **Dubbelavdraget i den öppnade grenen.**
   `avail = batt − batt_min − hard_floor` låser 20 % när båda är 10 %. Om
   `hard_floor` är det absoluta golvet ska det ersätta `batt_min`, inte
   adderas.
5. **Verifiera vakthunden.** Att `async_zero_battery()` faktiskt anropas
   från `async_unload_entry` i `__init__.py`, och att HA-automationen som
   nollar vid unavailable finns kvar.

**Acceptans:** Kör backtesten på 8 september-scenariot: SOC 59 %,
kvällsmål 73,7 %, köp 2,37 kr, batterikostnad 0,80. Före ändringen 0 W
urladdning, efter ändringen ≈1050 W. Och i drift: en kväll med SOC under
kvällsmålet och köppris över batterikostnaden ska ge verklig urladdning.

---

## STEG 1 — Uppmätta konstanter in
*en kväll*

Fyra tal som idag är antaganden och som sitter i varje beslut. Inget
arkitekturarbete, men de flyttar alla trösklar.

| Parameter | Idag | Ska vara | Grund |
|---|---|---|---|
| Rundgångsverkningsgrad | 0,87 (antagen) | 0,849 | 10 637 / 12 525 kWh, anläggningens egna AC-räknare |
| Cykelkostnad | 0,05 (gissad) | 0,05 (motiverad) | Garantin 10 år eller 10 000 cykler; kalendern binder vid 0,36–1,0 cykler/dygn |
| Säljpåslag | 0,06–0,07 | 0,065 | Lerum Energi, nätnytta 6,50 öre inkl. moms |
| Lasttakt i golvet | dygnssnitt, ev. klämt | dygnsprofil | natt 0,75–0,85 kW, dag 0,85–1,15 kW (aug 2026) |

1. **Kontrollera att 1,5 kW-klämningen är borta.** Den bröt 74 % av
   dygnen okt–mars; kallaste dygnet låg på 4,10 kW. Om `min(max(...), 1500)`
   finns kvar någonstans ska taket bort och golvet på 0,5 kW behållas.
2. **Lastprofil i stället för platt takt** — se "Steg 1A" nedan, utredd,
   inskriven och **implementerad** (v0.9.6).
3. **Dygnsbudget: dra bort dumpen, behåll desinficeringen** — se
   "Steg 1B" nedan, utredd, inskriven och **implementerad** (v0.9.5).
4. **Ny sensor: ackumulerade ekvivalenta cykler.** Så att antagandet om
   cykelkostnaden övervakas i stället för att förutsättas.
   **Implementerad (v0.9.4):** `sem_battery_equivalent_cycles`, se
   `docs/forbrukningsanalys.md`.

### Steg 1A — Lastprofilen: form skild från nivå

Arkitekturvalet: fönstret får inte bära nivån alls. Lasten delas i **form**
och **nivå**:

- **Form:** 24 timhinkar, median över ett rullande 21-dygnsfönster,
  normaliserade mot varje dygns egen summa (en fraktion av dygnet, inte
  ett absolutvärde), hållna konstanta över sina fyra kvartsprisslots.
  Kvartshinkar avfärdade — på den nivån är variationen termostatcykling,
  och varje hink får en fjärdedel så många observationer.
- **Nivå:** kommer från gradtimmodellen som redan finns i koden
  (`predicted_daily_kwh = base_dhw + k * max(0, t_bal - temp)`), INTE
  från fönstret.

Det löser årstidsproblemet utan ett vinterläge: ett fönster långt nog för
stabila hinkar (21 dygn) hinner aldrig med en köldknäpp, men nivån
reagerar på morgondagens temperaturprognos direkt eftersom den kommer
från en helt annan källa än formen.

P50 (median) används för planeringen i allmänhet, **P75 för reserven**
specifikt — den extra marginalen ("augustinattens premie") är bara
1–10 % jämfört med P50, en billig försiktighetsmarginal.

**Status: implementerad (v0.9.6).** `coordinator.py` samlar 24 timhinkar
per dygn (`_update_hourly_shape()`, medelvärde av `house_load_w` inom
varje timme) i en ny `{DOMAIN}_hourly_shape`-store (samma mönster som
`_daily_consumption_history`/`_pv_ratio_history`), normaliserar varje
fullständigt dygn mot sin egen summa och behåller max 21 dygn.
`_get_load_shape(percentile)` ger P50/P75 per timme över fönstret.
`energy_planner.py::build_plan()` tar två nya valfria parametrar
(`load_shape_p50`, `load_shape_p75`) och en `_load_kw_at(dt, shape)`-
hjälpfunktion som slår upp `predicted_daily_kwh × shape[timme]` när
formdata finns, annars faller tillbaka till den platta `hourly_load_kw`.
P75 används för reservberäkningen (`behov_kwh`, `net_solar_tomorrow_kwh`),
P50 för allmän planering (`is_dark`, `load_kwh`, nätladdningsval).
Verifierat: utan formdata reproducerar planen exakt samma golv som innan
ändringen (regressionssäkert); med syntetisk formdata (natt lägre, morgon
högre) ger golvet ett annat, högre värde som återspeglar den verkliga
fördelningen i stället för ett dygnssnitt. Formhistoriken byggs upp live
och tar upp till 21 dygn efter driftsättning innan den är fullt aktiv.

### Steg 1B — Dygnsbudgeten: bryt den självförstärkande slingan

Poängen med subtraktionen (inte tidigare tydligt formulerad): den finns
för att bryta en självförstärkande slinga. Dumpas 5 kWh idag blir
morgondagens budget 5 kWh högre, reserven större, nattladdningen större —
och mer dumpas. Inget annat är syftet. Åt andra hållet är felet farligare
(att felaktigt exkludera verklig obligatorisk förbrukning underskattar
reserven), så vid tveksamhet klassas energin som obligatorisk.

**Två rättelser från mätdata (2026-09-08):**

- Den ursprungligen föreslagna räknaren, `sensor.boiler_dhw_auxelecheatnrgcons`,
  har bara hel-kWh-upplösning och duger inte per cykel. Använd i stället
  `sensor.boiler_auxheaterstatus` (på/av) + `sensor.boiler_auxheaterlevel`
  (effektnivå i %). Regression av nivån mot räknaren över 22 dygn ger
  elpatronens märkeffekt till **8,83 kW** (driftnivå 66 % ⇒ 5,8 kW),
  stämmande inom räknarens egen kvantisering varje dygn.
- Desinficeringen kör inte pålitligt på en fast veckodag trots
  konfigurationen (`legionella_preferred_hour_start/end` styr en
  villkorad, inte kalenderbunden, körning) — bekräftat mot verklig
  switch-historik (`switch.boiler_dhw_disinfecting`): två påslag med
  exakt 7 dagars mellanrum, båda på en lördag, inte den konfigurerade
  veckodagen. Grinda alltså direkt på switchen, schemalägg aldrig på
  veckodag.

**En verklig (inte hypotetisk) detalj:** EMS-ESP-entiteterna går
`unavailable` ungefär två gånger per dygn. En oläsbar avläsning måste
hoppa över redovisningscykeln i stället för att räknas som noll (samma
princip som P3-2-fixen), och desinficeringsfönstret behöver 45 minuters
eftersläng (EMS-ESP rapporterar av något innan uppvärmningen faktiskt
stannar).

**Status: implementerad (v0.9.5).** `coordinator.py` spårar nu
elpatroneffekt (`auxheaterstatus` × `auxheaterlevel` × märkeffekt) och
exkluderar den ur dygnsbudgeten bara när den ligger UTANFÖR ett
desinficeringsfönster (switch på, eller av inom 45 minuter). Oläsbar
elpatron-status hoppar över cykeln; oläsbar legionella-status tolkas som
"kanske desinficering" (exkluderar inte, säkrare riktning). Verifierat
med sju fristående testfall (normal dump, legionella aktiv, inom/utanför
45-minutersfönstret, båda sensorerna otillgängliga var för sig, elpatron
av) — alla gav förväntat resultat.

**Acceptans:** Brytpunktsformlerna reproducerar tabellen:
`spot_hög > 1,178 · spot_låg + 0,215` på köpsidan, `+ 0,070` på säljsidan.
Fyra januari klassas som "cykla inte".

**Viktigt om hur brytpunktstestet ska köras (upptäckt 2026-09-08, gav
nästan en falsk röd flagga):** formeln ska aldrig testas mot dygnets
enskilda min/max-spotpris — bara mot **merit-order-viktat snitt av de
timmar batteriet faktiskt hade handlat till**. Batteriet tar timmar att
ladda/ladda ur (27 kWh vid 8 kW, 11 timmar att göra av med i ett hus som
drar 2,5 kW) — det handlar aldrig till den enskilt billigaste eller
dyraste kvarten. Mot 4 januari 2026 gav min/max (0,818/1,332) en falsk
"cykla"-signal (+7/+5 kr); det transaktabla snittet (0,863 laddning /
1,120 urladdning) ger korrekt "stå still" (−2/−4 kr, beroende på η) —
spridningen mellan enskilda extremvärden överskattar systematiskt,
ungefär en faktor två här. Reserven FÖRBJUDER för övrigt inte cykeln på
ett dygn som 4 januari — den KRÄVER den (60,3 kWh dygnsförbrukning mot
27 kWh batteri, ingen spekulativ cykling inblandad); det enda öppna är om
prisskillnaden täcker rundgångsförlusten, vilket den marginellt inte gör
just den dagen (2–3 januari gjorde den, med god marginal).

Det här är en genuin tvetydighet i den slutna formeln, inte ett fel i
implementationen: formeln är ett diagnostik-/handräkningsverktyg, giltigt
bara mot priser man faktiskt kan transagera till. I värdemodellen (steg 3)
försvinner tvetydigheten av sig själv, eftersom V per definition ÄR priset
i den marginella tilldelade sloten, inte dygnets högsta — samma
mekanism, bara redan rätt konstruerad. `pv_production_ratio`s roll i V
(steg 3) är separat: den höjer värdet på lagrad energi under osäkerhet så
systemet håller hårdare och köper tidigare — inte det som avgör om ett
enskilt dygn ska klassas "stå still".

---

## STEG 2 — Golvet blir en bana
*två–tre kvällar*

Den strukturella fixen. Golvet är idag ett skalärt tal som räknas fram
som nattens behov och sedan används som något som inte får röras — samma
tal i två motstridiga roller.

**Nu:** `export_floor_kwh` är konstant över hela natten. Vid rätt
dimensionerad reserv blir `avail = batt − min − golv` noll, och batteriet
vägrar täcka lasten reserven fanns för. Med taket på 0,85 får ett fullt
batteri leverera 4,1 kWh av 27.

**Mål:** Reservkravet räknas från varje slot och framåt och krymper med
natten. Batteriet dräneras längs kurvan och landar på min_soc ungefär när
solen tar över.

1. **Reservfunktion i stället för konstant.** `energy_planner.py`
   ```
   reserve_at(t) = Σ  max(0, last_kwh(s) − sol_p10_kwh(s))   för s ≥ t, s < takeover
                   × (1 + osäkerhetspåslag(pv_production_ratio))
   tillåten urladdning i slot t:
       avail = batt_kwh(t) − batt_min_kwh − reserve_at(t+1)
   ```
   Eftersom både batteriet och reservkravet minskar med samma kWh när
   lasten täcks blir `avail` positiv genom hela natten — vilket är hela
   poängen.
2. **Skyddsspärren kan utgå.** `_FLOOR_SAFETY_CAP_FRACTION` var en lapp
   mot att det skalära golvet kunde överstiga hela spannet. Med en bana
   kan det inte hända.
3. **Prisspärren kan utgå.** Option B var en lapp mot samma sak. Behåll
   den tills banan är verifierad i backtest, ta sedan bort den.
4. **Kvällsmålet härleds ur banan**, inte ur ett skalärt golv — eller
   utgår helt, eftersom steg 3 gör det överflödigt.

**Acceptans:** Backtest på en januarivecka: SOC-kurvan sjunker jämnt genom
natten och bottnar nära min_soc vid soluppgång, i stället för att stanna
på 26 kWh. Ingen natt slutar med batteriet över 50 % och nätimport under
pristoppen.

### Steg 2 implementerat (v0.9.7, 2026-09-09)

`energy_planner.py::build_plan()`:

- **`reserve_at(t)`** – ny lokal funktion. Bygger en suffix-summa
  (`_reserve_suffix`, en dict nyckel per floor_slot-start) av
  `max(0, last_kwh(s) − sol_p10_kwh(s))` för alla floor_slots, beräknad en
  gång per planeringscykel (O(n)). `reserve_at(t)` slår upp summan för
  första floor_slot med `start >= t` (linjärsökning, O(n) per anrop — trivialt
  vid ~112 slots/28h-horisont), adderar den icke-avtagande bufferten
  (2 kWh + `ev_reserve_margin_kwh`), multiplicerar med
  `_uncertainty_markup(pv_production_ratio)`, och klämmer mot
  `[hard_floor_kwh, batt_max_kwh]`. Faller tillbaka till en tidsproportionell
  uppskattning (`_load_kw_at(t, load_shape_p75) × återstående_timmar`) om
  `ps.slots` saknas.
- **`export_floor_kwh` = `reserve_at(now_a)`** – bevarat som externt kontrakt
  (DayPlan-fält, kvällsmåls-SOC, sensorer) men är nu en ÖGONBLICKSBILD av
  banan vid planeringstillfället, inte en konstant genom hela simuleringen.
- **Simuleringsloopen** använder banan istället för det skalära talet på tre
  ställen: dagtida självkonsumtion (`reserve_at(slot.end)`), nätladdningens
  mål (`reserve_at(slot.start)`), och mörk-grenens självkonsumtion
  (`reserve_at(slot.end)`, ersätter den gamla `_eff_floor`/"80 % av kommande
  2h sol"-hacken — redundant nu när `reserve_at` redan drar av `sol_p10_kwh`
  per slot i suffix-summan).
- **`_FLOOR_SAFETY_CAP_FRACTION` borttagen** (konstant + användning) enligt
  planens punkt 2 — motiveringen (en bana klämd mot `batt_max_kwh` kan inte
  strukturellt överstiga spannet) höll i backtest: högsta observerade
  golvvärde landade exakt på `batt_max_kwh` (30,38 kWh vid `battery_max_soc`
  99 %, `battery_capacity_kwh` 30,69 kWh i backtest-konfigurationen), aldrig
  över.
- **Option B (prisspärren) oförändrad** — kvar enligt planens punkt 3, tas
  bort när banan är verifierad mot en riktig vinterbacktest.

**Verifiering:** `testdata/backtest.py testdata/history` (samma ~10-dagars
fönster 2026-08-26–2026-09-05 som tidigare steg, se känd begränsning i
`testdata/history/Series info.txt` — en riktig januarivecka finns inte i
testdata ännu). Resultat:

- Ingen krasch, 91,3 % besparing mot referens utan batteri/styrning
  (jämförbart med tidigare stegs 93,4 % — skillnaden förväntad, olika
  fönster/kod, inte en regression i sig).
- **Golvet avtar mjukt över natten** i stället för att stå still: natten
  28–29/8 sjunker det rapporterade `export_floor_kwh` 5,24 → 3,07 kWh
  (00:00–06:00), natten 29–30/8 7,32 → 3,25 kWh (00:00–07:00) — i takt med
  batteriets SOC (t.ex. 82,1 % → 65,5 % samma fönster), ingen platå.
- SOC-spannet över hela perioden: 25,8–100 %. Bottnar INTE nära `min_soc`
  (20 % i backtestens config) eftersom perioden är sen sommar, inte januari
  — förväntat givet datagapet ovan, inte ett underkänt acceptanstest. Den
  kvalitativa delen av acceptanskravet (jämn nedgång, ingen platå) är
  uppfylld; den kvantitativa delen (botten nära min_soc en verklig
  vinternatt) kan först verifieras när en januarivecka finns i testdata.

---

## STEG 3 — Marginalvärdet V
*tre–fyra kvällar*

Kärnan. Ett tal ersätter nio konkurrerande trösklar, och de fyra besluten
blir jämförelser mot det talet.

1. **Beräkna V per planeringscykel.** `energy_planner.py`
   ```
   1. Projicera nettobehov per kvart:  deficit(s) = last(s) − sol_p10(s)
   2. Sortera framtida underskottsslots efter köppris, dyrast först
   3. Dela ut batteriets energi i den ordningen, begränsat av effekt per slot
      och av vad solen fyller på däremellan
   4. V = köppriset i den BILLIGASTE slot som fick tilldelning
      Räcker energin till alla underskott → V = bästa framtida säljpris
   ```
2. **Fyra beslutsregler.**
   ```
   V > sälj_nu            → ladda från solöverskott hellre än att sälja
   köp_nu > V              → täck huslasten från batteriet
   köp_nu + cykel < V      → nätladda
   sälj_nu > V + cykel     → exportera
   ```
   Reglerna är ömsesidigt uteslutande av konstruktion, eftersom
   köp > sälj alltid. Laddning och urladdning kan aldrig begäras
   samtidigt.
3. **Riskpåslaget flyttar in i V.**
   `V_effektiv = V × (1 + risk(pv_production_ratio))`. Ett högre V ger
   mindre export, sparsammare självkonsumtion och nätladdning vid högre
   priser — alla tre effekterna man vill ha när panelerna kan vara
   snötäckta.
4. **Exponera V som sensor**, tillsammans med vilken slot som satte
   marginalen. Utan den går ingenting att felsöka.
5. **Horisont 36–48 h.** Ingen prisprognos behövs: besluten som kräver
   morgondagens priser fattas på kvällen, när de finns.

**Acceptans:** V-sensorn ligger mellan bästa framtida säljpris och
dyraste framtida köppris i alla lägen. Backtest över ett år ger lägre
total kostnad än steg 2 — och januari fungerar utan specialfall.

### Steg 3 implementerat, INTE klart (v0.9.8, 2026-09-09)

`energy_planner.py::build_plan()` skrevs om: `reserve_at(t)` (steg 2) togs
bort, ersatt av en marginalvärdesfunktion V och fyra beslutsregler i
simuleringsloopen, exakt enligt punkterna ovan. Tre riktiga buggar
hittades och fixades under vägs (dokumenterade i kodkommentarer på
respektive plats):

1. **Rundgångsverkningsgrad saknades i laddningsjämförelsen.** Regel 1/3
   (V > sälj_nu / köp_nu+cykel < V) jämförde rakt mot V utan att ta hänsyn
   till att bara `eta_roundtrip`-andelen av en laddad kWh överlever till
   att kunna användas/säljas senare — en ~12 öres marginal räckte då för
   att motivera en hel laddcykel trots ~15 % förlust. Fixat genom
   `V_charge = V * eta_roundtrip`, bara på laddningssidans regler (2/4 tar
   UT redan lagrad energi, ingen ytterligare förlust där).
2. **Scarce/abundant-förväxling satte V=0,00.** Den ursprungliga
   tvågrensmodellen (köpsida vs säljsida) föll igenom till en
   initialiserad standard på 0,0 i flera edge-cases — bland annat exakt
   när batteriet låg UNDER sin egen bevarade reserv (en historisk
   startpunkt lägre än konfigurerat), det motsatta av "inget är knappt".
3. **Cirkularitet i solprognosen.** `_cap_by_time` (den kronologiska
   uppskattningen av hur mycket batteriet kan innehålla vid en framtida
   tidpunkt om det bara laddas av sol) använde optimistisk (p50) sol,
   vilket fick förmiddagens underskott att se lätt-täckta ut redan innan
   solen faktiskt kommit in — V kollapsade, och regel 1 sålde solöverskott
   direkt istället för att spara det till kvällen, vilket i efterhand
   gjorde antagandet falskt. Bytt till pessimistisk (p10/P75), samma
   konvention som resten av reservlogiken.

**Även efter alla tre fixarna: regression, inte förbättring.** Backtest
mot samma ~10-dagarsfönster som verifierade steg 0–2
(2026-08-26–2026-09-05): 45 % besparing mot referens, jämfört med steg
2:s 91 % på identisk data. Nätimport steg från 9,3 till 62,2 kWh,
nätexport från 16,2 till 172,4 kWh, batteriladdningen från sol sjönk från
293 till ~130–185 kWh — mitt-på-dagen-sol som borde laddat batteriet
inför kvällen exporterades direkt istället, upprepade gånger, trots tre
buggfixar avsedda att förhindra just det.

**Ursprunglig grundorsaksteori (2026-09-09, senare bara delvis
bekräftad):** V:s merit-order-allokering sågs bara se `_PLAN_HORIZON_H`
(48 h) framåt, vilket antogs vara den strukturella boven — se punkt 4
nedan för vad den faktiska dominerande orsaken visade sig vara.

#### Fjärde buggen (v0.9.9): fel bearbetningsordning i tilldelningen

Hittad via användarens kodgranskning, som misstänkte ett fjärde
strukturellt fel bakom regressionen (rätt instinkt, delvis annan
mekanism än den ursprungliga hypotesen). Verifierat med tillfällig
instrumentering: total accepterad volym i merit-order-tilldelningen låg
på **62–70 kWh mot fysiskt tillgängliga ~22 kWh** (batt_max minus
reserverat) — batterikapacitet räknades om och om igen.

Orsaken: `_opportunities` rankas efter VÄRDE (pris), men den ursprungliga
genomförbarhetskontrollen (`_used_before_t = sum(... if a_s.start <
s.start)`) drog bara ifrån redan accepterade tilldelningar med
KRONOLOGISKT tidigare starttid. Eftersom acceptans sker i värdeordning,
inte tidsordning, kunde en högvärderad möjlighet SENT i horisonten
accepteras INNAN en lågvärderad men kronologiskt TIDIGARE möjlighet ens
prövats — och reserverade då inget utrymme åt den. Ett konkret
motexempel (tre möjligheter A@t5, B@t10, C@t3, processade i den
ordningen eftersom deras VÄRDEN råkar rankas så) visar att den kumulativa
gränsen vid den SENASTE tidpunkten (t10) kan överskridas trots att varje
enskild kontroll "lokalt" såg ut att hålla sig inom gränsen.

**Fix:** ersatte engångskontrollen (`_cap_at_t - _used_before_t`) med en
riktig minsta-marginal-beräkning (`_slack_min_from(t_i)`) över HELA den
återstående horisonten från `t_i` och framåt, dynamiskt uppdaterad
(`_withdrawn_at`) efter varje accepterad tilldelning — samma princip som
steg 2:s `reserve_at(t)`-suffixsumma redan använder, fast här måste den
räknas om efter varje nytt accepterat uttag eftersom genomgången inte är
kronologisk.

**Resultat:** accepterad volym föll till fysiskt rimliga ~14–15 kWh.
Beteendet blev kvalitativt mycket bättre — batteriet laddas mot 100 %
under förmiddagen (`V·η > sälj` håller kvar solen istället för att sälja
den till bottenpris) och kvällens/nattens last täcks konsekvent från
batteri istället för nät (`köp 2,3–2,6 kr > V 1,3 kr → batteri`).
Besparingen steg från 45 % till **55 %** i samma backtest — fortfarande
under steg 2:s 91 %.

**Reviderad bedömning av "grundorsaken":** den ursprungliga
48-timmarshorisont-teorin var inte fel i sig (den kan fortfarande bidra
till återstående gap), men den var INTE den dominerande förklaringen till
45 %-siffran — den fjärde buggen (tilldelningsordningen) var det. Kvar
att förklara i det återstående gapet mot steg 2:s 91 %: modellen har
blivit betydligt mer aktiv med opportunistisk nätladdning (47
`grid_charge`-tillfällen mot steg 2:s 5) vars nettolönsamhet efter
rundgångsförlust inte är fullt verifierad, plus att horisontbegränsningen
fortfarande är oåtgärdad.

**Beslut (2026-09-09):** `energy_planner.py` lämnas i sitt nuvarande skick
(steg 3:s kod, `reserve_at(t)` borttagen) i git-historiken som
dokumenterat, overifierat arbete — **INTE driftsatt**. v0.9.7
(commit `a57cea2`) är den senast backtest-verifierade och säkra versionen.
Steg 3 kräver mer arbete innan det slår steg 2, troligen en av:
- Mycket längre planeringshorisont (kräver längre testdata än de ~10 dygn
  som finns idag, se `testdata/history/Series info.txt` — samma
  datalucka som blockerar steg 2:s fulla vinterverifiering).
- En separat mekanism för "skydda kapacitet bortom synhåll", t.ex. ett
  golv liknande steg 2:s `reserve_at(t)` som ALLTID gäller som ett golv
  under V (inte ersatt av V, utan V verkar OVANPÅ en bottennivå) —
  kombinerar båda stegens styrkor istället för att välja mellan dem.
- Eller en omprövning av om en enda skalär V per planeringscykel
  verkligen är rätt abstraktion för den här tariffstrukturen, givet hur
  stor köp/sälj-spreaden är.

#### Femte fyndet: en bugg i testverktyget, inte i planeraren (2026-09-09)

Under det fortsatta arbetet på att stänga gapet (cykelkostnad även på
regel 2 – i sig korrekt men utan mätbar effekt) upptäcktes att
`export`-slots i backtesten sålde till ett konstant golvpris (0,065
kr/kWh) i sammanfattningsstatistiken, trots att planerarens EGEN
`reason`-text visade helt andra, korrekt varierande priser (0,29–2,36
kr/kWh) för samma beslut. Spårat till roten: `testdata/backtest.py`:s
`SENSOR_MAP` hade `"sensor.nordpool_kwh_se3_sek_3_10_0_2": ("nordpool_raw",
lambda v: v / 100.0)` – transformen delar `v` med 100.0 utan att först
konvertera strängen (`_safe()` skickar alltid in rådata som str direkt
från CSV) till float. `str / float` kastar `TypeError`, `_safe()` fångar
den tyst och returnerar `None`, och `build_state()` faller tillbaka på
`vals.get("nordpool_raw", 0.0)` = 0.0 för VARJE rad. `state.buy_price_sek_kwh`
och `state.sell_price_sek_kwh` (och `spot_price_sek_kwh`) har därför varit
KONSTANTA (1,2325 / 0,065 kr/kWh, exakt avgifterna på ett nollpris) i
`apply_plan_executor()`s egna `econ_peak`/`prefer_sell`-omprövningar
genom HELA sessionen – inte bara i steg 3:s körningar.

Steg 0–2 märkte det aldrig eftersom deras dominerande beteende
(`cover_load`/`solar_charge` via `self_consume_ok`, en ren SOC-tröskel)
inte är prisberoende i executorn. Steg 3 är mycket mer prisberoende
(`grid_charge`/`export` styrs av `econ_peak`/`prefer_sell`, båda byggda
på de trasiga fälten) – så bara steg 3 fick fel resultat av det här,
trots att buggen själv är lika gammal som `build_state()`.

**Fix:** `lambda v: float(v) / 100.0`. Verifierat isolerat (rätt
varierande spotpris läses nu in) och i full backtest.

**Reviderat resultat (samma 10-dagarsfönster, nu med korrekt pris i
BÅDA leden):** procentandelen "besparing mot referens" blev instabil
efter fixen (referenskostnaden föll till nästan noll – rätt säljpris ger
referensfallets 301,5 kWh rå solexport mycket mer krediterad intäkt än
det gamla golvpriset gjorde, vilket gör division-mot-nästan-noll
missvisande). Jämfört istället i ABSOLUT nettokostnad över samma 10
dagar, samma verktygsfix i båda körningarna:
- **Steg 2** (commit `a57cea2`, med `reserve_at(t)`): −114,85 kr (dvs.
  114,85 kr i vinst över perioden).
- **Steg 3** (nuvarande kod, alla fem fynden ovan åtgärdade): −99,26 kr
  (99,26 kr i vinst).

Gapet är alltså ~15,6 kr över 10 dygn (~1,5 kr/dygn) — steg 3 når nu
**~86 %** av steg 2:s vinst, en helt annan bild än de tidigare (bugg-
förorenade) siffrorna "45 %" och "55 % mot steg 2:s 91 %" gav. De
tidigare procentsiffrorna i den här filen och i CHANGELOG.md/.sv.md för
v0.9.8/v0.9.9 var beräknade mot samma trasiga referens och bör läsas som
ORDNING (steg 3 sämre än steg 2, förbättrad av var och en av de fyra
tidigare buggfixarna), inte som exakta tal.

**Uppdaterat beslut:** steg 3 slår fortfarande inte steg 2, men gapet är
nu litet nog att vara värt att fortsätta stänga med riktade fixar
(t.ex. horisontbegränsningen eller nätladdningens lönsamhet) istället för
att anses kräva en ny grundarkitektur. INTE driftsatt än.

#### Sjätte fyndet: nätladdning och export nettade inte mot varandra (v0.9.11)

Undersökte varför affären (nätladda→exportera) knappt gick jämnt upp:
`grid_charge`-snittpriset (1,47 kr/kWh) låg FAKTISKT ÖVER export-
snittpriset (1,43 kr/kWh) i v0.9.10:s körning. Orsak: regel 4 jämför bara
`sälj_nu > V + cykel`, aldrig mot vad energin faktiskt kostade att lagra.
V räknas om varje planeringscykel mot en 48h-horisont som vandrar framåt
i tiden – ett beslut som var lokalt rationellt vid laddningstillfället
(V var högt då) kan fortfarande klara det enkla V-kravet vid
exporttillfället även om V sjunkit under tiden, eftersom kontrollen
aldrig minns vad som en gång motiverade laddningen.

`battery_avg_cost_sek_kwh` (planerarens redan existerande, men efter
steg 3:s omskrivning oanvända, parameter) är precis den broms som saknas
– samma princip Option B/steg 0–2 redan använde. Regel 4 ändrad till
`sälj_nu > max(V, battery_avg_cost_sek_kwh) + cykel`.

Kunde dock inte verifieras direkt: `testdata/backtest.py` hårdkodade
`EnergyState.battery_avg_cost_sek_kwh = 0.0` i `build_state()` – den
riktiga kostnadsackumulatorn (`BatteryAccumulatedCostSensor`, sensor.py)
finns bara i skarp drift. Lade till en motsvarande minispårare
(`sim_avg_cost_sek_kwh`) i P7-2:s framåtsimuleringsloop, som speglar
CLAUDE.md:s dokumenterade regel exakt: sol till batteri bokförs till
säljpris (alternativkostnad), nät till köppris, urladdning lämnar
snittkostnaden orörd (bara energipoolen den gäller för krymper).

**Resultat:** exportsnittpriset steg till 1,72 kr/kWh (färre men bättre
exportaffärer – 26,6 kWh istället för 34,0, alla nu över det riktiga
kostnadsgolvet). Nettokostnaden förbättrades från −99,26 till −102,87 kr
över samma 10 dagar. **Steg 3 når nu ~90 % av steg 2:s vinst**, upp från
~86 %. Gapet är ~12,0 kr/10 dagar.

Stickprov av kvarvarande "sälj direkt"-tillfällen (istället för att
ladda batteriet) visar inget uppenbart nytt fel – de flesta är antingen
batteriet redan fullt (inget annat val för överskottet) eller nära
brytpunkten. Kvarvarande gap bedöms nu bero mer på den redan
identifierade horisontbegränsningen (skyddar bara ~2 synliga nätter) än
på ytterligare trösklfel.

**Steg 4 pausat (2026-09-09).** Kartläggning inför steg 4 visade att
`prefer_sell`, `economic_peak`, `sell_solar_min_price` och
`evening_target_soc` inte bara finns i `energy_planner.py` (redan döda
sedan steg 3) — de finns som EGEN, AKTIV logik inuti
`apply_plan_executor()` (`energy_controller.py:980-1059`), det enda
skrivstället för batteriets börvärden i SKARP DRIFT (delat med
`testdata/backtest.py`). Steg 4:s tabell förutsätter att executorn kan
lita på planens `target_power_w` rakt av istället för att själv
omvärdera — men det kräver att V är tillräckligt pålitligt för det, och
V slår fortfarande inte steg 2 (55 % mot 91 %) och är inte driftsatt.
Beslut: fortsätt täppa till gapet i V (steg 3) innan `apply_plan_executor()`
rörs — annars ändras skarp, levande styrlogik baserat på ett ännu
overifierat V. Steg 4 återupptas när steg 3 slår steg 2 i backtest.

---

## STEG 4 — Riv det som blivit överflödigt
*en kväll*

Städningen är en del av vinsten. Varje kvarlämnad tröskel är en plats där
två regler kan säga olika saker.

| Tas bort | Ersätts av |
|---|---|
| `can_export` | Regel 4 |
| `export_sell_percentile` | Regel 4 |
| `export_min_sell_price` | Regel 4 |
| `export_min_solar_tomorrow_kwh` | V:s beroende av sol_p10 |
| `prefer_sell` / `sell_solar_min_price` | Regel 1 |
| `economic_peak` | Regel 2 |
| prisspärren (Option B) | Regel 2 |
| `evening_target_soc` | Reservbanan |
| `_FLOOR_SAFETY_CAP_FRACTION` | Reservbanan |

**Behålls:** `battery_min_soc` som hård fysisk gräns i executorn.
Fasskyddet och exporttaket. Force-lägena för hand. `_uncertainty_markup`,
men nu som påslag på V.

**Acceptans:** Ingen kodväg sätter längre både `battery_charge_power_w`
och `battery_discharge_power_w`. Avvikelseloggens mjuka matchningar kan
tas bort utan att loggen börjar larma.

---

## STEG 5 — Sommarens laddningstiming
*två kvällar*

Den enda posten i planen som är gratis: ingen extra cykel, ingen
förlust, ingen risk. Och den är inte implementerad idag.

**Nu:** `prefer_sell = sell_price >= 0,80 kr` — en fast tröskel som
avgör punktvis om en soltimme ska lagras eller säljas.

**Mål:** Batteriet fylls ändå under dagen. Valet är vilka
överskottstimmar som går in i det. Ladda i timmarna med lägst säljpris,
exportera i de högsta.

1. **Merit-order på säljsidan.** Rangordna dygnets överskottsslots efter
   säljpris stigande, fyll batteriet ur de billigaste upp till
   tillgängligt utrymme, exportera resten. Samma mekanism som
   urladdningens merit-order, spegelvänd.
2. **Extracykeln, villkorad.** Ladda ur mot nätet i dygnets dyra timmar
   och låt solen fylla igen i de billiga, när
   `sälj(topp) > 1,178 · sälj(botten) + 0,070`. Maj–augusti 2026 var
   villkoret uppfyllt 76 av 123 dygn.
3. **Morgontoppen före kvällstoppen.** Säljs på morgonen fyller dagens sol
   batteriet före kvällen. Säljs på kvällen står det tomt in i natten.
   Alternativkostnaden skiljer även när priset inte gör det.
4. **Kräv p10-täckning** innan extracykeln startas — annars fylls
   batteriet inte igen.

**Fasskyddet blir skarpt här:** 8 kW batteriexport plus solproduktion i
morgontimmarna närmar sig både 14 kW-gränsen och 20 A per fas.
Fasskyddet testar redan `abs()`, men exporttaket
`min(14 000, 3 × 18 × 230) − solar_w` måste finnas innan sommarläget
aktiveras.

**Acceptans:** Backtest juli: samma exporterade energi som idag men
högre intäkt. Ingen fas överstiger 20 A i någon riktning över hela
sommaren.

---

## STEG 6 — Bilen som planerbar last
*två kvällar*

12 kWh, 1-fas 16 A på L1, alltså högst 3,7 kW och drygt tre timmar från
tom till full. Liten i energi, men den enda lasten som konkurrerar med
batteriets nattladdning om samma fas.

1. **In i optimeringen som schemalagd last** med energibehov och
   deadline. Den konkurrerar på samma måttstock som allt annat: ladda i
   de slots där köppriset är lägst inom deadlinen.
2. **Fas 1-samordning.** 8 kW batteriladdning plus 2,4 kW huslast är
   redan 15 A per fas. Bilens 3,7 kW på L1 spränger gränsen. Planeraren
   måste fördela dem i tid, inte lita på att `_apply_phase_limits` städar
   upp reaktivt.
3. **Invariant kvar:** i autoläge laddas bilen bara ur solöverskott som
   återstår efter huslasten, eller i uttryckligt schemalagda billiga
   slots. Batteriets urladdning överstiger aldrig husets underskott
   exklusive bilen.

**Acceptans:** En vinternatt med både billaddning och batteriladdning
håller alla tre faserna under 20 A, utan att fasskyddet behöver ingripa.

---

## STEG 7 — Huset som värmelager
*störst, och störst vinst*

Värmepumpen är 61–62 % av förbrukningen i januari och februari. Batteriet
räcker till en dryg tredjedel av en januarinatt. Det här är den enda
resursen som är i rätt storleksordning för vintern.

1. **Identifiera husets termiska parametrar** ur befintlig data. Tretton
   rumstemperaturer, dämpad utetemperatur och pannans energiräknare i
   timupplösning sedan oktober 2025. En regression ger tidskonstant och
   kWh per grad utan att någon behöver frysa en natt. Kontrollera först
   vilka rumsgivare som har `state_class` och alltså långtidsstatistik.
2. **Håll elpatronerna utanför.** Förutsättningen för allt annat.
   `number.boiler_tempparmode` (10 °C idag) sänks mot 0…−5 °C,
   `auxheaterdelay` förlängs under dyra slots. Använd helpern
   "Eltillskott aktivt" som facit under intrimningen.
3. **Rumsvis strategi, inte en gemensam offset.** Sovrummen (18 °C) är
   tomma under kvällstoppen och används på natten som är billig — de tål
   störst nedreglering just när det är dyrast. Vardagsrummet (21 °C) rörs
   minst. Förslag att utgå från: ±1 °C i vardagsrum och kök, ±1,5 °C i
   sovrum och sällan använda rum, inget i badrum.
4. **Lönsamhetsregel med COP.** Förvärmning kostar extra eftersom
   verkningsgraden sjunker vid högre framledning. Du har redan helpern
   "VP verkningsgrad".
   ```
   vinst   = (pris_topp − pris_förvärm) × kWh_förskjuten
   kostnad = kWh_förskjuten × (1/cop_förvärm − 1/cop_normal) × pris_förvärm
   ```
5. **Soldrift via pannans egen väg.** `number.boiler_pvmaxcomp` (0–25 kW,
   står på 0) är kompressorns maxeffekt vid PV-överskott — pannans
   inbyggda soldriftläge, oanvänt idag.
6. **Dumpen och desinficeringen blir schemalagda laster.** Dumpa till
   varmvatten när V < sälj_nu — samma jämförelse som avgör om batteriet
   ska laddas. Desinficeringen är en bunden last med deadline, som
   planeras in i den billigaste sloten inom sitt sjudygnsfönster i
   stället för att starta på ett tröskelvillkor.

**Acceptans:** Under en vintervecka minskar andelen
uppvärmningsenergi som köps under dygnets dyraste fyra timmar, mot en
jämförbar vecka utan styrning, utan att inomhustemperaturen lämnar det
tillåtna spannet och utan att Eltillskott aktivt går igång under
återhämtningen.

---

## STEG 8 — Validering
*löpande*

Simulatorn finns redan och delar `apply_plan_executor` med driften,
vilket är exakt rätt konstruktion. Det som saknas är att peka den mot
hela året.

**Känd lucka (upptäckt 2026-09-08, inte i original­planen):** `backtest.py`
läser sina tidsstämplar från `price_quarterhour.csv`, som bara täcker
~10 dagar (2026-08-26–2026-09-05). Värmepumpsdatan (nästan ett helt år)
och en förlängd Nordpool-prisserie (`nordpool_price_extended.csv`,
2025-11-08–2026-08-31) finns nu i `testdata/history/`, men husast/sol/
temp/SOC/nätfaser täcker fortfarande bara samma korta fönster. Steg 8:s
"kör mot hela året"-acceptans kräver motsvarande långa exporter av de
serierna innan den går att uppfylla fullt ut — se
`testdata/history/Series info.txt`.

1. **Två syratester.** Januari 2026 med de nolldygnen (15 dygn med exakt
   0,0 kWh, 11 i följd 4–14 jan — bekräftat, se
   `docs/forbrukningsanalys.md` avsnitt 8), och juli 2026 med maximal
   export. Ett system som klarar båda utan specialfall är klart.
2. **Mätetal i kronor** mot en referens utan batteri och utan styrning.
   Kör varje steg mot samma period så att vinsten per ändring blir
   synlig.
3. **Kör om steg 0 till 3 i tur och ordning** mot samma data. Om något
   steg inte förbättrar utfallet är antingen steget eller antagandet fel
   — och då vill man veta det innan nästa steg byggs ovanpå.

**Acceptans:** Simulatorn reproducerar en verklig vecka inom rimlig
felmarginal på köpt och såld energi. Först då säger den något om
framtiden.

---

## Invarianter — enhetstester, inte kommentarer

- `battery_charge_power_w` och `battery_discharge_power_w` är aldrig
  båda skilda från noll.
- Batteriets urladdning överstiger aldrig husets underskott exklusive
  bilen. Detta är hela garantin för mål 5.
- Ingen fas överstiger 20 A i vare sig import- eller exportriktning
  efter klämning.
- Batteriet går aldrig under `battery_min_soc`.
- Vid negativt säljpris är nettoexporten mot nätet noll eller negativ.
- Varje börvärde till Sonnen har ett motsvarande värde skrivet inom de
  senaste fem minuterna, annars nollas det.
- Planeraren beslutar policy, executorn klämmer mot fysik. Ingen tröskel
  finns på båda ställena.

---

## Vad vi medvetet inte bygger

- **Export som vinststrategi.** `köp − sälj = 0,25 · spot + 1,17` —
  egenanvändning slår alltid försäljning för energi huset kommer att
  förbruka. Export är en restpost, utom i sommarens timingmanövrer.
- **Effekttariffhantering.** Det finns ingen effekttariff på
  abonnemanget. Fasgränsen är en säkringsfråga, inte en ekonomisk.
- **Prisprognos.** Besluten som kräver morgondagens priser fattas efter
  kl 13, när de publicerats.
- **Ett vinterläge.** Säsong är indata, inte en kodväg.
