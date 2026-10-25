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

---

## Kodkommentarer flyttade från koden (2026-10-24, v0.9.63)

Längre förklaringar och incidentberättelser som tidigare låg inline. Koden
har kvar en rad med varför och en hänvisning hit (t.ex. `Se docs/decisions.md P04.`).
Texten är oförändrad; rad- och versionshänvisningar i den gäller när den skrevs.

### energy_planner.py

#### P01 – Oanvända build_plan-argument

```text
solar_forecast_tomorrow_kwh och solar_takeover_dt konsumeras inte
längre av logiken nedan (v1.0 steg 3): V:s merit-order-allokering
ser redan morgondagens sol_p10 per slot via ps.slots. Kvar i
signaturen bara för att inte behöva röra coordinator.py:s anrop
innan steg 4:s städning. battery_avg_cost_sek_kwh används
däremot fortfarande, som en broms mot regel 4 (export) – se
kommentaren där. current_outdoor_temp_c/weather_forecast (se
_simulate_drought_days) är fristående från ps.slots/V och valfria
– None/tom lista ger exakt tidigare beteende.
```

#### P02 – Lastnivå: max mot verklig förbrukning

```text
Samma mönster som _auto_mode() (v0.7.6): predicted_daily_kwh
(temperaturmodell) täcker bara uppvärmning/varmvatten, inte den
generella hushållsbaslasten – ta max mot verklig förbrukning.
Föredrar det 7-dygns rullande snittet (netto exkl. extra
varmvatten-energi, se coordinator._get_rolling_consumption_kwh())
framför en enda dags gårdagssiffra – en enstaka ovanligt hög dag
(elpatron, EV, varmvatten) ska inte ensam blåsa upp golvet för hela
natten (docs/forbrukningsanalys.md avsnitt 8). Faller tillbaka till
gårdagen ensam tills 7 dygn hunnit rulla över sedan funktionen
driftsattes. Projektionen använder house_load_avg_w (glidande
medel) om den finns, annars momentan house_load_w – en enstaka
kokplatta/dusch ska inte läsas som "hela natten".
```

#### P03 – Lastform skalas mot _eff_daily_kwh

```text
v1.0 steg 1A: lastens FORM (24 timhinkar, rullande <=21-dygnssnitt,
normaliserat per dygn – se coordinator._get_load_shape()) skild från
NIVÅN. P50 för allmän planering, P75 för underskottsprojektionen i
V (en smalt högre försiktighetsmarginal). Faller tillbaka till den
platta hourly_load_kw tills 21 dygns formhistorik hunnit byggas
upp, eller för enskilda timmar utan täckning i fönstret.

Live-incident 2026-09-23: en mild kväll (14,9°C, 0 gradtimmar) gav
predicted_daily_kwh=1,0 kWh (bara varmvattnets baslinje - gradtim-
modellen täcker ALDRIG hushållets generella baslast, se
kommentaren vid _eff_daily_kwh ovan). Formen skalades mot den
råa 1,0 kWh:en istället för _eff_daily_kwh, så varenda timmes
projicerade last - dag som natt, inklusive morgondagens pristopp
06:30-09:00 - kollapsade mot nästan noll. Underskottet (deficit_kwh)
blev aldrig >0,01 kWh, så regel 2 (köp>V → täck ur batteriet)
triggade aldrig alls; hela natten och morgondagens toppris blev
"idle" istället för cover_load. _eff_daily_kwh reagerar ändå direkt
uppåt på en kall dag (gradtimmen slår igenom via max()) - den
skyddar bara mot att NIVÅN kollapsar under den redan uppmätta
baslasten på en mild dag, exakt samma golv hourly_load_kw redan
har några rader ovan.
```

#### P04 – Marginalvärdet V och _cap_by_time

```text
--- v1.0 steg 3: marginalvärdet V --------------------------------
"Vattenvärdet" av en kWh i batteriet just nu: allokera den
tillgängliga batterienergin (ovan hård gräns + buffert) mot en
rankad lista av framtida "möjligheter" (täcka ett underskott ELLER
exportera, se _opportunities nedan) i värdeordning, dyrast först,
begränsat av effekt per slot OCH av vad solen hinner fylla på
innan respektive slots tidpunkt (inte en delad pool som kan tas i
fel kronologisk ordning). V = värdet (köp- eller säljpris) i den
billigaste möjlighet som fick tilldelning.

_cap_by_time: kronologisk, prisfri simulering av hur mycket
batteriet SOM MEST kan innehålla vid varje framtida tidpunkt om
det bara någonsin laddas av sol (aldrig urladdas) – den övre
gränsen "vad solen fyller på däremellan" sätter för en slot sent i
horisonten. Använder PESSIMISTISK sol (p10) och P75-last, samma
försiktighetskonvention som resten av reservlogiken – inte
förväntad (p50) sol. Ett optimistiskt antagande här skapar en
cirkularitet: om capacity-prognosen antar att all sol fångas ser
kvällens underskott lätt-täckt ut redan mitt på dagen, V faller,
regel 1 säljer solen direkt istället för att spara den – vilket
gör antagandet falskt i efterhand. Verifierat i backtest: gav en
tydlig regression (53% besparing mot steg 2:s 91%, mitt-på-dagen-
sol som skulle laddat batteriet för natten exporterades istället
för nästan inget). Se docs/v1_implementation_plan.md, Steg 3
implementerat.
Live-incident 2026-09-22: batteriet stod redan på 96% SOC vid
planeringstillfället, så _running_cap (redan vid taket) kunde
aldrig växa - trots att Solcast lovade 41 kWh (P10!) sol under
dagen. Morgonens och kvällens urladdningsmöjligheter tvingades då
konkurrera om exakt samma, orörliga pool (nuvarande nivå minus
reserv) i _slack_min_from nedan - kvällens högre pris vann alltid,
och morgontoppen (billigare men FÖRE middagssolen) fick ingenting
trots att en morgonurladdning fysiskt hinner fyllas på av
middagssolen långt innan kvällen. _banked_extra_kwh fångar upp
solöverskott som annars hade klippts bort av min()-taket här och
gör det tillgängligt som utrymme för SENARE möjligheter - exakt
det utrymme en tidigare urladdning skulle frigjort för solen att
fylla i.
```

#### P05 – Golv innan morgondagens priser publicerats

```text
Live-incident 2026-09-22, del 2: Nordpool publicerar imorgondagens
priser runt 13-14, så future_slots (byggt uteslutande av ps.slots)
tar effektivt slut vid midnatt fram tills dess - merit-ordern är
då helt blind för imorgon, oavsett hur säker Solcast-prognosen
redan är. Användaren påpekade korrekt: vi vet redan att solen inte
räcker imorgon, prisokunskapen ändrar inte DEN sanningen. Så länge
ps.slots inte når in i morgondagen, höj golvet med det förväntade
underskottet (dagens lastnivå som proxy för imorgon, minus
Solcasts redan kända prognos) - ett EXTRA, TEMPORÄRT skydd som
försvinner automatiskt så fort riktiga priser (och därmed regel
2/3/4:s egen, prismedvetna hantering av morgondagen) kommer in.
max(), inte +, mot det befintliga golvet - samma stapelskydd som
_drought_markup redan använder mot _uncertainty_markup ovan.
Kapat vid batt_max_kwh - kan aldrig reservera mer än batteriet
fysiskt rymmer, till skillnad från v1:s drought-golv (borttaget)
som saknade den spärren och mättade nästan varje vinterdag.
```

#### P06 – En gemensam merit-order

```text
Merit-order över ALLA framtida "möjligheter" en kWh batteri kan
användas till – inte två separata pass (köpsida/säljsida) som
visade sig kunna falla mellan stolarna och sätta V=0.0 (t.ex. när
batteriet exakt räckte till alla underskott utan någon marginal
kvar för säljsidans egen allokering, eller när batteriet redan låg
UNDER den bevarade reserven vid horisontens start så ingenting
kunde tilldelas alls – båda upptäckta i backtest, se
docs/v1_implementation_plan.md, Steg 3 implementerat).

Varje slot bidrar med upp till två "möjligheter" efter varandra,
ur samma effektbudget: först att täcka slotens eget underskott
(värde = köppris, det den sparar), sedan – med vad som blir över
av effekten – att exportera (värde = säljpris, det den tjänar).
Rankas dyrast/mest värdefullt först, vilket automatiskt prioriterar
lasttäckning före export när båda konkurrerar om samma kWh (köp>sälj
alltid – matchar CLAUDE.md:s prioritet 2 före 3 utan specialfall).
```

#### P07 – Kronologisk marginal-tracker

```text
Kronologisk marginal-tracker: en kandidat vid t_i tas bara emot om
den ryms UNDER TAKET VID VARJE framtida tidpunkt >= t_i, inte bara
vid t_i självt. En tidigare version jämförde bara mot summan av
REDAN accepterade möjligheter med start < t_i – men genomgången
sker i VÄRDEORDNING, inte kronologisk ordning, så en senare (i
tiden) hög-värderad möjlighet kunde accepteras FÖRE en tidigare
lågvärderad möjlighet ens prövats, utan att reservera utrymme åt
den – battericap räknades då flera gånger om. Verifierat i
backtest: total accepterad volym låg på 62–70 kWh mot fysiskt
tillgängliga ~22 kWh (batt_max−reserverat) innan den här fixen.
```

#### P08 – Regel 4:s öronmärkning

```text
Regel 4:s motsvarighet till regel 3:s solprognos-fix (docs/
v1_implementation_plan.md): hur mycket av DEN HÄR sloten fick
merit-ordern faktiskt tilldelat som exportmöjlighet (tier2,
värderad till s.sell_sek)? Utan det här slaget tömde regel 4
batteriet ner till golvet varje gång dagens pris klarade
tröskeln, oavsett om merit-ordern redan reserverat samma
kapacitet åt en bättre möjlighet senare i horisonten – exakt
samma sorts blind fläck som regel 3 hade på köpsidan, bara att
merit-ordern här redan VET (via _accepted) hur mycket som är
tryggt att släppa nu, den användes bara inte av exekveringen.
```

#### P09 – Regel 2:s öronmärkning

```text
Regel 2:s motsvarighet till regel 4:s öronmärkning ovan (tier1,
värderad till s.buy_sek) - live-incident 2026-09-16: V var korrekt
och hade redan öronmärkt kapacitet åt morgontoppen 07:00-07:15 i
merit-ordern, men regel 2 kollade bara det platta reservgolvet
(_reserved_kwh), aldrig vad som specifikt var reserverat åt just
DEN sloten - så vanlig självkonsumtion timme efter timme på natten
(varje enskild timme klarade sitt eget köp>V+cykel-test) åt upp
samma kapacitet morgontoppen redan hade fått, långt innan toppen
ens kom. Batteriet var tomt vid 5-tiden istället för att räcka
till 07:00.
```

#### P10 – Torkrisk-påslag

```text
v2, "Torkrisk-påslag från SMHI:s väderprognos": ett prospektivt
komplement till _uncertainty_markup (som bara reagerar
RETROAKTIVT på redan uppmätt låg produktion). Ersätter v1:s
separata, obundna kWh-golv (`_drought_reserve_kwh`, borttaget) –
det gav en dokumenterad regression eftersom husets dygnslast
(30-70+ kWh) vida överstiger batteriets kapacitet, så ett absolut
underskottsgolv mättade nästan varje vinterdag, inte bara vid
genuina torkor. max(), inte +, håller den nya signalen inom samma
redan verifierat säkra tak (3,0×) _uncertainty_markup redan
använder. Se docs/v1_implementation_plan.md.
```

#### P11 – V_charge: η och inget torkpåslag

```text
Regel 1 och 3 LADDAR ny energi in i batteriet – bara
eta_roundtrip-andelen av det överlever till att kunna användas/
säljas senare, så tröskeln för att spara/nätladda måste vara
strängare än för att ta UT redan lagrad energi (regel 2/4, ingen
ytterligare förlust där, den skedde redan vid laddningstillfället).
Utan den här faktorn (första implementationsförsöket) blev
regel 3+4 tillsammans en nästan fri arbitrage-loop – 12 öres
marginal räckte för att motivera en hel laddcykel trots ~15 %
rundgångsförlust, vilket i backtest gav SÄMRE resultat än steg 2
(62 % besparing mot 91 %) istället för bättre. Se
docs/v1_implementation_plan.md, Steg 3 implementerat.

V_charge bygger MEDVETET INTE på V (med torkpåslaget) utan räknas
om från grunden utan _drought_markup: kalibreringssvepet i docs/
v1_implementation_plan.md visade att torkpåslaget på köpsidan
kostade mer än det sparade även vid den mest konservativa
trösklingen (planade ut på 3,6 % mot 3,9 % utan mekanismen alls)
– systemet nätladdade för ivrigt dagar innan gratis sol ändå var
på väg (t.ex. 2-5 feb 2026, 8-27 kWh/dygn inför torkan 6-7 feb).
Torkrisk-signalen ska bara göra systemet mer obenäget att GÖRA
SIG AV MED energi (regel 2/4, via V), inte mer angeläget att
KÖPA ny (regel 1/3).
```

#### P12 – Säljsidans merit-order för solöverskott

```text
v1.0 steg 5, punkt 1+3: merit-order på säljsidan för dagens
solöverskott. Regel 1:s huvudloop nedan går igenom future_slots
KRONOLOGISKT och fyller batteriet giriga – så länge V_charge>sälj
och rum finns. Det kan fylla från en förmiddagsslot med
medelmåttigt säljpris och sedan sakna rum för en billigare (mer
värd att spara, t.ex. mitt på dagen vid solöverskott) slot som
råkar komma senare kronologiskt. Förbered istället en
prioritetsordning: rangordna alla överskottsslots som klarar
V-tröskeln efter säljpris STIGANDE (billigast/mest värt att spara
först) och dela ut det tillgängliga rummet (batt_max − nuvarande
batt_kwh) i den ordningen. Huvudloopen använder sedan
min(verkligt rum just då, denna prioritetsandel) som tak – aldrig
mer generöst än endera, men respekterar rangordningen när rummet
är den bindande begränsningen. Samma mekanism ger effektivt även
punkt 3 ("morgontoppen före kvällstoppen") gratis: en dyrare
förmiddagsslot som konkurrerar om samma rum med en billigare
eftermiddagsslot förlorar prioritetsordningen, exporteras direkt
istället – vilket lämnar rum kvar åt den billigare eftermiddags-
solen att fylla batteriet med.
Rummet delas bara mellan slots i SAMMA sammanhängande dagsljus-
fönster – inte hela 48h-horisonten. Annars kan en billigare slot
imorgon felaktigt reservera bort rum från en dyrare men ändå
värd-att-spara slot idag, trots att natten emellan (regel 2:s
egenförbrukning) redan hinner frigöra nytt rum. Kandidatlistan
avbryts vid första genuina mörka underskottsslot efter nu.
```

#### P13 – Regel 3 billigast först

```text
Regel 3-kandidater (idle-slots som klarar köp+cykel<V_charge) samlas
här istället för att laddas direkt i kronologisk ordning - annars
tar loopen bara FÖRSTA slot som klarar tröskeln, inte den BILLIGASTE
inom fönstret (live-incident 2026-09-15: V_charge steg till 2,87
kr/kWh, varpå praktiskt taget alla kvällstimmar klarade tröskeln,
och planen började nätladda direkt kl 21:15 för 2,19 kr/kWh istället
för att vänta på natten nästan-gratis timmar efter midnatt).
Allokeras billigast-först i ett andra pass nedan.
```

#### P14 – Regel 3 drar av väntad sol

```text
Ladda bara den del av room_kwh som solen (p10 blandat
mot p50, se _GRID_CHARGE_SOLAR_BLEND) INTE redan väntas
fylla gratis inom horisonten – _fill_cap_end_kwh visar
hur fullt batteriet blir av enbart sol vid horisontens slut.
Utan den här spärren nätladdade regel 3 för fullt även
när prognosen samtidigt visade rikligt med sol bara
timmar bort (live-incident 2026-09-10, se
docs/v1_implementation_plan.md) – V-beräkningen VET om
framtida sol (via _cap_by_time/_opportunities), men
regel 3 läste aldrig den kunskapen innan den här fixen.
```

#### P15 – Regel 4 bromsas av battery_avg_cost

```text
Regel 4:s tröskel är max(V, battery_avg_cost) + cykel, inte
bara V. V beräknas om varje planeringscykel mot en 48h-
horisont som vandrar framåt i tiden – en affär som var
lönsam när energin laddades (V högt då) kan se lönsam ut
för export senare även om V sjunkit under tiden, eftersom
V bara jämförs mot NUET, inte mot vad energin faktiskt
kostade. Verifierat i backtest: grid_charge-snittpriset
(1,47 kr/kWh) låg FAKTISKT ÖVER export-snittpriset
(1,43 kr/kWh) innan den här spärren – handeln var i
praktiken ett nollsummespel trots att varje enskilt beslut
följde V korrekt vid sitt eget tillfälle. battery_avg_cost
(bokförd kostnad, se sensor.py:s ackumulator) är den enda
tillgängliga bromsen mot just den glidande-horisont-effekten.
```

#### P16 – EV-schemat

```text
v1.0 steg 6, punkt 1: bilen som schemalagd last. Ett fristående
merit-order-schema (INTE kopplat till batteriets V eller
_opportunities – EV-energin drar inte på batteriets kapacitet,
det är en oberoende nät-/soldragning med sin egen deadline):
rangordna slots fram till deadline efter köppris STIGANDE, fyll
behovet från de billigaste först, begränsat av bilens maxeffekt.

Punkt 2 (fas-1-samordning) hanteras här bara som en enkel, säker
regel: undvik att lägga EV-laddning i samma slot som batteriets
egen grid_charge (den enda batteriåtgärden som aktivt drar NY
nätkraft samtidigt – 8 kW batteri + 3,7 kW bil är exakt den
kombination Steg 6 själv varnar för). Ingen fullständig per-fas-
modell finns ännu i planeraren (bara en skalär batterieffekt, ingen
fasuppdelning) – det kräver mer indata (max_current_per_phase,
spänning, bilens effektiva faser) än vad build_plan() tar emot
idag. Se docs/v1_implementation_plan.md, Steg 6 implementerat.

Punkt 3 (invarianten "bilen laddas bara ur solöverskott eller
uttryckligt schemalagda slots") är en EXEKVERINGSregel, inte en
planeringsregel – kräver att coordinator.py/energy_controller.py
faktiskt läser och respekterar det här schemat, vilket INTE görs
här (se stoppet inför att röra levande styrlogik, samma skäl som
steg 4 pausades).
```

### energy_controller.py

#### C01 – Absorptionstrappan vid negativt pris

```text
── Negativt säljpris just nu: absorptionstrappan (P4-2) ────────
Steg, KASKADVIS (varje steg äter av det som blir kvar efter
föregående, inte ömsesidigt uteslutande – annars nås steg 2+
aldrig så länge batteriet bara är under 100% SOC, vilket är nästan
alltid, oavsett om batteriets maxeffekt räcker för hela överskottet):
OBS: remaining_w räknas mot vad EV-steget BER om (MAX_EV_CURRENT), inte
vad _apply_phase_limits() i slutändan faktiskt beviljar – i sällsynta fall
med samtidig fasbegränsning kan strypningen därför bli något för snäll
(kvarvarande export missas), men aldrig farlig: fasskyddet klämmer ändå
till den verkliga effekten oavsett vad trappan trodde skulle absorberas.
  1. Batteri – full effekt (nät ELLER sol, oavsett – man får betalt för att äta)
  2. Varmvatten
  3. Värme (etapp 5 – kräver P5-3:s börvärdesförskjutning, ej implementerat än)
  4. Bil
  5. Strypning av växelriktaren (P4-1-kalibrerad: procent räknas mot
     märkeffekt, ~30-50s svarstid)
```

#### C02 – Kvällsfylling mot Solcast

```text
Kvällsfylling: Solcast-prognosen för platsen avgör om solen räcker
för att fylla batteriet innan solnedgång.

Logik: summera förväntad solenergi (kWh) från Solcast-slots fram till
solnedgång. Om den summan understiger vad batteriet behöver för att nå
evening_min_soc → starta kvällsfylling nu, oavsett säljpris.

Fungerar automatiskt för alla årstider eftersom Solcast känner till
exakt panelvinkel och plats.
```

#### C03 – Formad lastprognos i kvällsmålet

```text
Formad prognos istället för platt snitt: samma _load_kw_at-
mönster som energy_planner.py redan använder för V/V_charge
(load_shape_p75, 21-dygns rullande P75 per timme). Ett platt
hourly_load_kw × hours_dark sprider förbrukningen jämnt över
hela mörkret - missar morgontoppen helt och underskattar
behovet (upptäckt 2026-09-15: evening_target_soc räknades
till 11,5% trots att verklig förbrukning tömde batteriet
till 5%-golvet timmar innan solen tog över). Faller tillbaka
på det platta snittet per timme utan formdata (idag alla
natt-/morgontimmar, tills v0.9.37s fortlöpande sparning
hunnit bygga upp riktig historik för dem).
```

#### C04 – Varmvattendumpens överskottskrav (ersatt 2026-10-24, se posten om Extra VV)

```text
v1.0 steg 7 punkt 6 (första halvan): dumpa till varmvatten istället
för att sälja, när V_charge < sälj_nu - regel 1:s ELSE-gren (samma
jämförelse som avgör om batteriet laddas, se heat_planner.
should_dump_to_hot_water()s docstring). extra_hot_water är en ren
PÅ/AV-brytare som drar elpatronens FULLA effekt oavsett hur litet
överskottet var vid triggertillfället - ett tidigare 100W-golv
kunde slå på en 6 kW-brytare på ett par hundra watt sol, vilket i
praktiken importerade resten från nät/batteri (upptäckt 2026-09-14,
flimrade på/av i skymningen). Kräv därför att överskottet täcker
en stor andel (self.extra_hot_water_min_surplus_ratio) av elpatronens
verkliga effekt innan dumpen tillåts.
```

#### C05 – EV billigast-timmar

```text
EV billigast-timmar-laddning: vintern har ofta för lite sol för att
sol-loopen ovan (som bara laddar bilen från solöverskott) någonsin
aktiverar den. Speglar CLAUDE.md:s vinter-mål ("billigast-timmar-
laddning av batteri OCH bil") utan ett separat driftläge – körs
alltid i auto när sol är låg och priset just nu är billigt. Måttlig
ström (inte MAX_EV_CURRENT): batteriet har fortfarande prioritet
(CLAUDE.md prioritet #2), och fasskyddet (_apply_phase_limits)
klämmer till om fas 1 (EV + batteri + elpatron) blir för belastad.
```

#### C06 – Negativt pris i exekutorn

```text
P4-2: negativt-pris-trappan i _auto_mode() har redan satt det
fullständiga beslutet (batteri/varmvatten/bil/strypning i
kaskad). Dagsplanen känner inte till negativa priser alls (den
planerar mot percentiler av dagens EGNA priser) - utan den här
spärren skrev raderna nedan blint över trappans beslut med
planens vanliga cover_load/export-logik varje cykel.
_auto_mode()s eget fasskyddsanrop körde med update_memory=False
(se dess kommentar) eftersom DEN HÄR funktionen äger det
slutgiltiga minnesskrivandet i auto-läge - så det måste göras
här på varje returväg, annars fryser minnet kvar på ett gammalt
värde (eller, värre, på ett värde från en tidigare cykel vars
eget minnesskrivande också hoppades över).
```

#### C07 – Ingen prefer_sell vid solar_charge

```text
Live-incident 2026-09-22: batteriet stod stilla på 16% SOC i
40+ minuter med 7+ kW solöverskott tillgängligt, trots att
planen sa solar_charge. `prefer_sell` (menat för idle/
cover_load-grenen nedan, där PLANEN inte alls räknat med
överskott just nu) jämförde säljpriset mot ett statiskt,
föråldrat golv (evening_target ~11,5%, bara min-SOC+EV-
marginal) - helt orelaterat till planens FAKTISKA mål för
dagen (t.ex. 99% SOC). Eftersom batteriet redan råkade ligga
över det gamla golvet och säljpriset var över 0,80 kr,
river exekutorn tyst upp planens egen, betydligt bättre
informerade V_charge>sälj-bedömning för just den här sloten.
Planen har redan avgjort att spara är mer värt än sälja här -
exekutorn ska inte lägga ett eget, kruddare veto ovanpå det.
```

#### C08 – Fasskyddets baslast

```text
Elmätarens avläsningar innehåller redan alla nuvarande laster:
sol, batteri, EV, värmepump, elpatron.
Vi applicerar bara delta för det som beslutet ändrar.

Baslasten byggs från VERKLIG uppmätt ström (grid_current_lX) när den
finns, inte bara effekt/spänning - en riktig CSV-jämförelse
(2026-09-11) visade att effekt-approximationen missar reaktiv/
tomgångsström (upp till +8,7A avvikelse vid låg effekt, konvergerar
mot korrekt vid hög last). Strömsensorn rapporterar bara magnitud,
så importriktningen (tecken) tas fortfarande från effekten.
current_a <= 0 (ej konfigurerad/inget state) faller tillbaka på
gamla beteendet: effekt rakt av.
```

#### C09 – Fasskyddets batteribaslinje

```text
Batteri: ta bort nuvarande effekt, lägg till beslutets effekt (per fas).
Baslinjen är SEM:s EGET senast skickade kommando (_last_battery_command_w),
inte Sonnen-batteriets egen statusåterläsning (state.battery_power_w).
Den sensorn uppdaterar oregelbundet (uppmätt 2026-09-14: 422/465
mellanrum = 30s, men 14 st 60s, en 120s) och är inte synkad med
SEM:s egen 30s-cykel eller med elmatare_current_lX (baslasten ovan),
som hinner med snabbare. Diskrepansen gjorde att fasskyddet
systematiskt underskattade batteriets redan pågående bidrag -
loads[ph] fick både den redan-höjda verkliga strömmen OCH nästan
hela det nya 8000W-målet ovanpå, vilket gav falska 20A+-utslag och
en självförstärkande pendling mellan fullt 8000W och hårt klämt
värde var 30:e sekund. SEM:s eget kommando är känt med noll
fördröjning och alltid exakt en cykel gammalt (konsekvent),
istället för en extern sensor vars ålder varierar oförutsägbart.
```

#### C10 – Fasminnet uppdateras en gång per cykel

```text
Spara det FAKTISKT klämda kommandot till nästa cykels baslinje
(se kommentaren vid current_batt_per_phase ovan). update_memory=False
för anrop vars beslut kan skrivas över senare SAMMA cykel (_auto_mode()s
egna anrop, när apply_plan_executor() sedan ersätter batteridelen) -
annars stör det preliminära beslutet minnet innan det slutgiltiga
anropet hinner läsa föregående cykels VERKLIGA värde (upptäckt
2026-09-14: gav samma 8000W↔klämt-pendling som v0.9.34 skulle löst,
fast nu orsakad av anropsordningen istället för Sonnen-sensorlagg).
```

---

## 2026-10-24 – Extra VV: kompressor eller patron, och dumpens regler (v0.9.64)

**Mätt (IVT AirModule 9 kW, 21 Extra VV-starter 14–24 okt, ute ~2–15 °C):**

| Tank vid start | Pannan |
|---|---|
| 45,0–45,3 °C | bara kompressorn (`hpactivity` = hot water), ~3,5 kW / ~15 A på L2 |
| 48,0–48,1 °C | kompressorn först, patronen när kompressorn slutat |
| 48,9–56,5 °C | patronen direkt (~20 s), steg 1+2 = 66 % ≈ 5,8 kW, kopplad mellan L1 och L3 (13,4 A på båda) |

Värmepumpens fasströmsensorer: `sensor.ivt_phase_a_current` = L2 (kompressorn), `_b`/`_c` = L1/L3 (patronen ligger mellan dem, så ordningen spelar ingen roll). Strömmarna kommer ~7 s före EMS-ESP:s stegstatus.

**Fel som rättades:**
- Fasskyddet räknade alltid Extra VV som patron på L1/L3. När kompressorn gick på L2 och kontrollern ville stänga av (minimitiden höll den på) drogs 3 kW bort från L1 och L3 → falsk "export 20 A" → batteriets urladdning ströps till 39–600 W i ~10 min medan huset köpte 3,9 kW (24 okt 14:24–14:34). Den verkliga överlasten på L2 (18,5 A) syntes inte.
- Dumpens egen last låg i huslasten, så nästa cykel såg "inget överskott": av/på med minimitiden som takt (14 okt: 13 starter på 3 h).
- Minimitiden lades på efter fasskyddet.

**Beslut (alternativ 1):** dumpen startar bara om överskottet täcker HELA den last pannan väntas välja (kompressor under 47 °C, annars patron) och värmen är värd mer än säljpriset (patron: snittköp 24 h / 3,5; kompressor: snittköp). Pågående dumps last räknas tillbaka. Täcker överskottet under 50 % av lasten avbryts dumpen förbi minimitiden; fasskyddet stänger av den på samma sätt. Fasskyddet räknar med uppmätt ström när sensorerna finns.

**Kvar:** gränsen 47 °C och COP 3,5 är mätta i milt väder; kontrollera vid kyla. Kompressorn ensam (~15 A) ligger nära fasmarginalen på L2 (17 A − 2 A) när ingen sol går att räkna av.

---

## 2026-10-25 – Fas B, steg B0: ny baslinje (v0.9.66)

Infört i HA (alternativen) och i `testdata/backtest.py` SETTINGS samtidigt: `grid_fees_sek_kwh` 0,389, `energy_tax_sek_kwh` 0,36 (köp = 1,25 × spot + 0,937 enligt faktura), `battery_min_soc` 8, `eta_roundtrip` 0,848. Kontroll i drift: `sensor.smart_energy_manager_buy_price` 0,9363 kr vid spot 0.

Fel hittat på vägen: `number.smart_energy_manager_battery_min_soc` återställde sitt gamla värde (5) efter reload och skrev det till kontrollern medan planeraren läste 8 ur options. Rättat i `number.py` (återställ bara om nyckeln saknas i options); live-läget rättat med `number.set_value` 8.

**Baslinjer B0** (oraklet = `testdata/oracle_dp.py`, perfekt framförhållning, samma batterimodell):

| Period | Dagens planerare | Cykler | Orakel | Andel |
|---|---|---|---|---|
| Sommar (`testdata/history`, 10 d) | 146,25 kr | 5,53 | 211,94 kr | 69 % |
| Vinter (`history_jan_apr2026`, 2026-01-01…04-30) | 833,81 kr | 25,60 | 1 899,91 kr | 44 % |

Besparingen sjönk mot förut (157 / 914 kr) för att köpet nu är ~0,30 kr/kWh billigare – lagrad energi är mindre värd. **Godkänt för DP-planeraren (B2/B3): vinter ≥ 1 330 kr (≈ 70 % av oraklet), sommar ≥ 146 kr.**

---

## 2026-10-25 – Fas B, steg B1: indata (v0.9.67)

**Batteriets effekt mot SOC** (timstatistik jan–apr och aug–okt 2026, ~2 700 timmar):
- Laddning: full effekt (~8 kW) ända upp till 98 % SOC; vid 99 % ~3,9 kW. Max-SOC är 99, så ingen effektkurva behövs.
- Urladdning: följer beordrad effekt fullt ned till ~10 %; 8–10 % ungefär 70 % av beordrat; vid 8 % stopp (5 W, sett i flera timmar 20–21 sep).
- ⇒ DP-planeraren räknar med konstant 8 kW åt båda hållen inom 8–98 %, hård nedre gräns 8 %.

**Lastnivå per dygn från väderprognosen:** `coordinator._daily_load_kwh_by_date()` räknar gradtimmodellen per prognosdygn (SMHI dygnsmedel (max+min)/2, bara kommande dygn – idag behåller dämpad temperatur + desinficeringstillägg); `EnergyPlanner.build_plan(daily_load_kwh_by_date=…)` skalar lastformen per dygn med förbrukningsgolvet kvar. Backtest `--temp-oracle` (verklig dygnsmedeltemp som perfekt prognos): sommar 146,25 → 146,25 kr, vinter 833,81 → **825,56 kr (−1 %)**. En kallare morgondag höjer V och V-planeraren håller/köper mer – samma strukturfel som HANDOFF 4.1. **Beslut: inte inkopplad i drift med V-planeraren; kopplas in med DP-planeraren och mäts där.**

## 2026-10-25 – Fas B, steg B2: DP-planeraren (v0.9.68)

**Modell:** tillstånd = (slot, lagrad energi i 0,1 kWh-steg mellan min-SOC och max-SOC), bakåtrekursion med numpy över hela den kända prishorisonten. Last = dygnsnivå × p50-timform (samma som V-planeraren), sol = Solcast p50; första sloten proratas. Kostnad = köp × import − sälj × export + 0,05 kr/kWh × urladdning. η 0,848 delas lika per ben (√η). Reserv = min-SOC + 2 kWh + EV-marginal – under den bara laddning (reservbeslutet, fråga 1, tas i B3). Slutvärde = (E − e_min) × medelköp sista 24 h × √η; saknas morgondagens priser värderas energi upp till morgondagens underskott till max köp.

**Översättning till exekutorns åtgärder:** export klassas bara när batteriexporten > ett steg × √η, och `target_power_w` = −exporten/h (bara exportdelen – exekutorn laddar ur |mål| + husets underskott, att ta med lasten hade dubblat urladdningen). Annars `cover_load`; k = 0 med underskott → `hold_battery`. V = differens av cost-to-go, `marginal_value_charge` = V × √η.

**Backtest:**

| | V (B0) | DP | DP + `--load-oracle` | DP + `--temp-oracle` | Orakel |
|---|---|---|---|---|---|
| Sommar | 146,25 | 153,30 (72 %) | 170,06 (80 %) | – | 211,94 |
| Vinter | 833,81 | 1 033,74 (54 %) | 1 175,13 (62 %) | 1 031,21 | 1 899,91 |

Cykler: sommar 6,89 (V 5,53), vinter 43,87 (V 25,60; med perfekt last 49,30). Exekutorn överstyr sällan: 0 econ_peak, 169 verkligt överskott, 56 verkligt underskott. Öppet: 1 616 planerade grid_charge-slots men bara 1 199 laddade – utreds i B3.

**Slutsatser:** DP slår V tydligt på vintern (strukturfelet 4.1 är borta – proben laddar ur genom båda topparna) men når inte acceptansmålet 1 330 kr. Lastprognosfelet kostar ~140 kr; temperaturnivån per dygn ger ingenting (≈ 0), så den förblir urkopplad. Resterande gap mot oraklet ligger i horisont/slutvärde/reserv → B3.

## 2026-10-25 – Fas B, steg B3: mätfel, lastnivå, reserv (v0.9.69)

**Mätfel i backtesten (påverkade båda planerarna):**
1. *Fasbas:* simuleringen delade controller med körningen på historikens SOC och fasskyddet utgick från uppmätt faseffekt, inklusive det verkliga batteriet. Exempel 3 jan 01:45–04:00: historiken laddade 4,3–5 kW, simuleringen ville ladda 7,8 kW, fasskyddet såg ~13 kW batteri och ströp till 0; SOC stod på 51,6 %. 409 av 2 225 DP-grid_charge-slots (vinter). Efter rättning (egen `sim_controller`, fas += (sim_förra − verklig)/3, strömmar nollade så att effektvägen används) återstår 2 – verkliga fasgränser.
2. *Lastformens timme:* byggdes per UTC-timme, slogs upp per lokal timme (`dt.astimezone().hour`, samma i produktion); dessutom skrev varje kvart över den förra så formen var timmens :45-kvart. Nu lokalt, medel per timme. Produktionen (`_update_hourly_shape`, `now.hour`) var rätt.
3. *Slutlager:* lagerjusterad besparing = besparing + (E_slut − E_start) × snittköp × √η. Oraklet har samma slutvärdering (`value = −(E − E_start) × snittköp × √η`).

Rättningarna 1–2 ändrar baslinjerna: V sommar 146,25 → 147,80, vinter 833,81 → 880,64.

**Lastnivå:** dygnsnivån i drift är i praktiken max(gårdag, 7 d), eftersom gradtimmodellen (bas 1, k 2,39, t_bal 12) ligger ~20 kWh/dygn under helhusförbrukningen (snitt 45,8 kWh vintertid). Uppmätt fel per dygn (jan–apr + aug–sep):

| Nivå | Bias | MAE (kWh) |
|---|---|---|
| gårdagens last | +0,15 | 5,59 |
| 7 d snitt | +0,76 | 6,33 |
| max(gårdag, 7 d) – drift | +3,05 | 6,70 |
| 7 d + 2,53 × ΔDD (t_bal 15) | +0,05 | 3,23 |

Kalibreringen (bas 13,2, k 2,53 vid t_bal 15) är gjord på samma data den mäts på (2 parametrar, ~160 dygn). I DP används nivån utan golv (`daily_load_kwh_by_date` är auktoritativ i `DPPlanner`).

**Backtest, DP, kalibrerad nivå** (besparing / lagerjusterad):

| Variant | Sommar | Vinter | Cykler vinter |
|---|---|---|---|
| reserv 0, cykel 0,05 | 172,55 / 207,27 | 1 324,89 / 1 336,45 | 57,8 |
| + `--temp-oracle` | 172,45 / 207,18 | 1 326,88 / 1 338,46 | 57,5 |
| reserv 2 | 166,62 / 201,35 | 1 302,25 / 1 313,76 | 56,5 |
| reserv 0, cykel 0,10 | 170,07 / 204,80 | 1 325,29 / 1 337,00 | 53,5 |
| reserv 0, perfekt last | 175,53 / 209,67 | 1 359,10 / 1 369,78 | 58,4 |
| V (jämförelse) | 147,80 / 171,37 | 880,64 / 872,30 | 27,6 |
| Orakel | – / 237,82 | – / 1 898,72 | |

Utan kalibrerad nivå (golvet kvar): reserv 0 1 222,20, reserv 2 1 196,02; före lastformsrättningen reserv 4 1 160,27 och syntetiskt tredje dygn −22 kr mot snittköp-slutvärdet.

**Slutsatser:** acceptansmålet (70 % av oraklet vintertid) är nått med kalibrerad nivå. Kvarvarande gap mot oraklet (~530 kr) är horisonten (DP ser ≤ 36 h priser) och exekutorns verklighetsklämning – inte lastprognosen (33 kr kvar). Slutvärde = snittköp sista 24 h behålls. Öppna beslut (användaren): reservmarginal (fråga 1) och cykelkostnad – besparingen i backtesten räknar inte slitage, så 0,10 med 4 färre cykler är i praktiken bättre om slitaget är verkligt.

**Beslut 2026-10-25 (användaren):** reservmarginal **0 kWh** över min-SOC och cykelkostnad **0,10 kr/kWh** för DP. Införda som `DPPlanner`-standard; backtesten ger DP 0,10 om inte `--cycle-cost` anges, V behåller driftens 0,05. Driftens `cycle_cost_sek_kwh` (HA-inställning, 0,05) ändras först när DP tar över (B5) – den styr V-planeraren i dag. Utfall med kalibrerad nivå: sommar 170,07 / 204,80, vinter 1 325,29 / 1 337,00 kr, 53,5 fullcykler vintertid.

## 2026-10-25 – Fas B, steg B4: skuggläge (v0.9.70)

**Vad:** `coordinator` bygger `_day_plan_b` med `DPPlanner` direkt efter V-planen (samma utlösare: var 15:e min eller när prisschemat ändras), i `hass.async_add_executor_job` och egen `try`. Exekutorn och alla befintliga sensorer läser bara `_day_plan`. Sensorer `plan_b_*` (unique_id `sem_plan_b_*`) delar klass med V-planens via `_plan()`.

**Indata som skiljer sig från V:** `daily_load_kwh_by_date` = `calibrated_daily_levels(_daily_consumption_history, idag, _forecast_mean_temp_by_date)` – samma funktion som backtestens `--load-level calib` (backtesten anropar den nu, siffrorna oförändrade 170,07 / 1 325,29). Inget EV-schema (`ev_energy_needed_kwh`), ingen `load_shape_p75`, inget torkriskpåslag – DP har inga motsvarigheter än. Reserv 0, cykel 0,10 (DP-standard).

**Temperatur i dygnshistoriken:** `_temp_samples`/`_yesterday_avg_temp` fylls bara när ingen dämpad utetempsensor finns, och live finns en (`sensor.thermostat_dampedoutdoortemp`). Därför en egen ackumulator av momentan utetemp (`sensor.boiler_outdoortemp`, samma som kalibreringen) som skrivs som `mean_temp_c` vid dygnsskiftet. En omstart mitt på dygnet ger medel av resten av dygnet – godtaget.

**Skillnad backtest–drift:** backtesten kalibrerades och körs mot total huslast; drift använder `net_kwh` (exkl. dump). Dumpen är SEM-styrd last som inte ska planeras in, så drift är det riktiga – nivån kan ligga något lägre än i backtesten dumpdagar.

**Utvärdering före B5:** jämför plan A/B i planloggen (`plan_b` per ögonblicksbild) och `daily_load_kwh_by_date` mot faktisk dygnsförbrukning under minst en–två veckor.

