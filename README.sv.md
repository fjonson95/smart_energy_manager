# Smart Energy Manager – HACS Integration

![Version](https://img.shields.io/badge/version-0.5.47-blue)

En HACS-integration för Home Assistant som optimerar egenförbrukning av solenergi med batteri, EV-laddare och elpanna/varmvattenberedare.

## Nyheter i 0.5.47

- **Fix: tröskel för ekonomisk topp-självkonsumtion sänkt från 2,0× till 1,2×** – v0.5.44-villkoret (`köppris ≥ bästa_laddpris × 2,0`) krävde en mycket stor prisskillnad innan batteriet laddade ur under dyra timmar. Priser som 3,17 kr/kWh nu vs 2,37 kr/kWh kl 02:45 (kvot 1,34×) blockerades fortfarande av både 2,0×- och den mellanliggande 1,5×-tröskeln. Tröskeln sätts till 1,2× så att batteriet kringgår kvällsgolvet när det kostar ≥ 20% mer att köpa nu jämfört med billigaste kommande laddslot — en spread som alltid är lönsam som arbitrage (0,80 kr/kWh nettvinst med kvällens priser). Beslutsanledningens suffix är nu `×1.2`.

## Nyheter i 0.5.45

- **Fix: proaktiv export blockeras när kommande natelektricitet är dyrare** – exportlogiken sålde batterienergi under dagen (t.ex. till 1,50–2,10 kr/kWh) utan att kontrollera om kommande mörka timmar skulle kräva att köpa tillbaka el till ett högre pris (t.ex. 3,77 kr/kWh). Det var en förlustsaffär: sälja billigt, köpa dyrt. Fix: ett nytt `_export_price_ok`-villkor beräknas innan exportgrinden: `säljpris ≥ max(köppris bland mörka slots i golvperioden) × 0,9`. Om nuvarande säljpris är under 90% av toppköppriset kommande natt blockeras export — energin är mer värdefull att spara för kvällens självkonsumtion. 90%-faktorn tillåter export när priserna är nära (t.ex. sälj 1,80 vs natt 1,90 — liten vinst värd att ta). Samma filter på morgonexport och speglat i `EnergyPlanner` (high-slots filtreras till de med sälj ≥ 90% av toppnattköppris). Kvällens fall: sälj 2,10 < 3,77 × 0,9 = 3,39 → export blockerad ✓.

## Nyheter i 0.5.44

- **Fix: batteriet stod stilla under dyra kvällstoppet — nätet täckte huslast till 3,77 kr/kWh** – `evening_target_soc`-vakten (SOC-golvet som skyddar nattreserven) blockerade korrekt självkonsumtion när batteriet låg under 71% målet. Men den gjorde ingen ekonomisk skillnad mellan billig natelektricitet (0,30–0,50 kr/kWh) och dyr topptid (3,77 kr/kWh på kvällen). Fix: ett nytt ekonomisk-topp-villkor kontrollerar om nuvarande köppris ≥ 2× billigaste kommande laddpris (`ps.best_charge_slot.buy_sek`). När sant sänks effektivt SOC-golv till `battery_min_soc` — batteriet laddar ur för att täcka huslasten nu, och möjlighetsladdningslogiken köper tillbaka billig el senare under natten. Kvällens fall: 3,77 / 1,25 = 3,0× → ekonomisk topp aktiv → batteriet laddar ur 1 041W istället för att importera. Beslutsorsaken visar `| Självkonsumtion XXXW (ekonomisk topp Y,YY>Z,ZZ×2)` när denna väg är aktiv.

## Nyheter i 0.5.43

- **Fix: batteriet stod stilla under morgonens pristoppet medan vi väntade på sol** – när `wait_for_solar` är aktivt (sol väntas inom 2h) blockerade `evening_target_soc`-vakten ändå självkonsumtionsurladdning. Batteriet stod på ~39% SOC medan huset importerade från nätet till morgontoppriset (~1,26 kr/kWh), trots att solen skulle fylla reserven långt innan kvällen. Fix: när `wait_for_solar = True` och `solar_next_2h_kwh > 0` sänks den effektiva kvällsgolvets tröskel med 80% av förväntad soladdning (`0,8 × solar_next_2h_kwh / kapacitet × 100%`). Med 5,7 kWh förväntad sol och 33 kWh kapacitet sänks tröskeln från 39,7% till ≈ 22%, vilket tillåter batteriet att täcka morgonlasten. 80%-faktorn ger säkerhetsmarginal om moln minskar faktisk produktion. Beslutsanledningen visar nu `(sol X.X kWh/2h)` när denna väg är aktiv. Samma logik är speglad i `EnergyPlanner`: mörka slots tittar 2h framåt och sänker golvet med 80% av förväntad sol, så planen korrekt återspeglar controllerns morgonbeteende.

## Nyheter i 0.5.42

- **Fix: EnergyPlanner-simuleringen dekrementerade inte batteriet vid självkonsumtion** – framåtsimuleringen i `energy_planner.py` genererade `cover_load`-slots (sol < hus, dagtid) och mörka `idle`-slots (natt) utan att minska `batt_kwh`. Planen överskattade därmed tillgänglig batterienergi under hela dygnet, vilket ledde till uppblåsta exportprognoser och en felaktig SOC-kurva. Fix: båda fallen simulerar nu batteritappning via självkonsumtion — batteriet laddar ur för att täcka husunderskottet (eller hela huslasten på natten) så länge `batt_kwh > batt_min_kwh + export_floor_kwh`. Exportgolvsskyddet speglar controllerns `battery_soc > evening_target_soc`-kontroll och skyddar natttäckningsreserven. När batteriet är vid golvet visas sloten som nättäckt med en tydlig förklaring.

## Nyheter i 0.5.41

- **Fix: batteriet laddade aldrig ur för självkonsumtion — all huslast ovanför sol köptes från nätet** – självkonsumtionsblocket fanns men krävde `köppris > 0,20 SEK/kWh`. Under sommaren när spotpriserna är låga uppfylldes aldrig det kravet. Batteriet stod still hela dagen och varje watt huslasten ej täckte av solen importerades från nätet. Fix: prisgrinden är borttagen. Lagrat sol-el är alltid bättre än nätimport oavsett aktuellt spotpris — batteriet laddar nu ur för att täcka husunderskott när `battery_soc > evening_target_soc` och `battery_soc > battery_min_soc`. Evening_target-vakten skyddar kvällsenergi­reserven som tidigare.

## Nyheter i 0.5.39

- **Nytt: number-entiteter för proaktiva exporttrösklar** – två nya justerbara parametrar exponeras nu som `number`-entiteter i Home Assistant: `Proaktiv export prispercentil` (50–100%, steg 5, standard 75 — export triggas när säljpris ≥ denna percentil av dagens priser) och `Proaktiv export absolut minpris` (0,00–3,00 SEK/kWh, steg 0,05, standard 0,70 — alltid-exportgolv oavsett percentil). Båda uppdaterar controllern omedelbart utan omstart och gäller så länge HA kör (återställs till konfigurationsflödesvärden vid omstart).

## Nyheter i 0.5.38

- **Fix: plan executor urladdningssensor visade värde för idle/cover_load-slots under kvällsgolvet** – v0.5.36 lade till `evening_target`-skyddet bara för `export`-slots. För `idle`- och `cover_load`-slots (batteriet täcker husunderskott på natten) saknades samma skydd — sensorn returnerade fortfarande `house_deficit_w` även när `battery_soc ≤ evening_target_soc_pct`. Skyddet gäller nu alla urladdningsåtgärder: alla slots returnerar 0 W när `battery_soc ≤ evening_target`.

## Nyheter i 0.5.37

- **Fix: "Bästa urladdningstimmen" laddade ur under kvällsgolvet efter solnedgången** – huslast-urladdningsvägen (`if not export_active … battery_soc > battery_min_soc`) skyddade bara mot absolut min SOC. Efter solnedgången sjunker `solar_w` under 100 W och `evening_fill = False` (solkontroll misslyckas) även när `battery_soc < evening_target_soc`. Runt 19:35, med batteriet på ~50% och kvällsmål 53,3%, triggade "Bästa urladdningstimmen" (toppris-köpperiod) och laddade ur 1 384 W för att täcka huslasten — drog under natttäckningsgolvet och orsakade min-SOC-urladdning under natten. Fix: lade till `battery_soc > evening_target_soc` i villkoret så batteriet skyddas under nattkravet även när `evening_fill` är tillfälligt False (ingen sol).

## Nyheter i 0.5.36

- **Fix: proaktiv export ignorerade `battery_min_soc` i energikontroll och laddade ur under golvet** – exportskyddet `_battery_energy_kwh > _export_floor_kwh` jämförde *total* batterienergi (t.ex. 51% × 33 kWh = 16,83 kWh) mot golvet (10,98 kWh) och drog slutsatsen att 5,85 kWh var tillgängliga att exportera. Men bara energin *ovan* min SOC är användbar: `(51 − 20)% × 33 = 10,23 kWh < 10,98 kWh golv` → inget att exportera. Controllern laddade ur med 4,9 kW in i kvällspristopparna trots att batteriet redan låg under natttäckningsgolvet. Samma bugg fanns i `EnergyPlanner.exportable_kwh`. Fix: båda beräkningarna använder nu `användbar_kwh = (batteri_soc − battery_min_soc)% × kapacitet`. Plan executor urladdningssensorn får också ett `evening_target`-skydd: för `export`-slots returnerar den 0 W när `battery_soc ≤ evening_target_soc_pct`.

## Nyheter i 0.5.35

- **Fix: `evening_target_soc` exkluderade `battery_min_soc`, vilket gav grov underskattning** – formeln `evening_target_soc = kvällsbehov_kWh / kapacitet × 100` behandlade batteriet som om all kapacitet vore användbar. Men `battery_min_soc = 20%` är ett absolut golv — de understa 6,6 kWh är aldrig tillgängliga. Resultat: 11,5 kWh kvällsbehov gav 34,9% mål, men vid 34,9% är bara `(34,9 − 20) / 100 × 33 = 4,9 kWh` faktiskt användbart — knappt hälften av behovet. Batteriet tömdes till min SOC varje natt även från 49–51% start-SOC. Fix: `evening_target_soc = min(battery_max_soc, battery_min_soc + kvällsbehov / kapacitet × 100)`. Med samma 11,5 kWh: `20 + 34,9 = 54,9%`, ger `(54,9 − 20) / 100 × 33 = 11,5 kWh` användbart — exakt vad som behövs. Samma fix applicerad på `EnergyPlanner.build_plan()`.

## Nyheter i 0.5.34

- **Fix: `evening_target_soc` underskattades på soldagar — batteriet tömdes på natten** – det dynamiska kvällsmålet beräknades som `(_eff_daily_kwh / 24) × mörktimmar + 2 kWh`, där `_eff_daily_kwh = max(prognostiserat, gårdagens förbrukning)`. På soldagar är `yesterday_consumption_kwh` bara nätuttaget (~7 kWh) → ~0,29 kW i snitt — fast faktisk nattlast är ~0,9–1,2 kW. Resultat: `evening_target_soc ≈ 17%`, batteriet låg alltid över det → `evening_fill = False` → controllern exporterade sol istället för att ladda. Batteriet tömdes sedan nattetid från ~49–51% till min SOC. Fix: `hourly_load_kw` klämms nu mot `min(max(daglig snitt, huslast / 1000, 0,5), 1,5)`, samma som v0.5.27-fixet för exportgolvet. Med huslast ~1,24 kW blir kvällsmålet ~53%, vilket triggar `evening_fill = True` (49% < 53%) och tvingar controllern att lagra sol innan den säljer.

## Nyheter i 0.5.33

- **Fix: Plan executor laddning använde planens `evening_target_soc_pct` istället för controllerns** – `prefer_sell`-kontrollen jämförde batteri-SOC mot planens `evening_target_soc_pct` (beräknad från `solar_takeover_dt`, som kunde vara satt till dagens sol ~11:15 → gav bara ~16,5% mål). Controllern beräknar sitt `evening_target_soc` annorlunda — söker i `ps.slots` efter första slot efter solnedgången med sol ≥ husbelastning, faller tillbaka på soluppgång + 3h, vilket ger ett realistiskt mörkerperiodsunderlag (~39% vid 33% batteri). Det fick sensorn att visa 0 W (prefer_sell=True, evening_fill=False) medan controllern laddade på fullt överskott (evening_fill=True). Fix: `evening_target_soc` läggs nu till i `ControlDecision`, sätts i `_auto_mode()` efter den dynamiska beräkningen, och sparas i `coordinator.data` som `evening_target_soc_pct` — ersätter planens värde. Laddningssensorn speglar nu controllerns eget `evening_fill`-resultat.

## Nyheter i 0.5.32

- **Fix: Plan executor laddning ignorerade `prefer_sell`-logiken** – för `solar_charge`-slots visade sensorn alltid batteriets laddningsestimat, men controllern hoppar över laddning och exporterar sol när `sell_price ≥ sell_solar_min_price AND NOT evening_fill`. Det gav sensorn t.ex. 4 000 W medan controllern faktiskt exporterade med 0 W laddning. Fix: `sell_solar_min_price` (från controllerns konfiguration) och `evening_target_soc_pct` (från dagsplanen) sparas nu i coordinator-data. Sensorn beräknar `prefer_sell = sell_price ≥ sell_solar_min_price OCH battery_soc ≥ evening_target_soc` och returnerar 0 W när sant — speglar controllerns exportbeslut. Attributen exponerar `prefer_sell`, `evening_fill`, `sell_price` och `sell_solar_min_price` för full transparens.

## Nyheter i 0.5.31

- **Fix: Plan executor laddning ignorerade EV-laddningens prioritet** – sensorn visade `min(solöverskott, batteri_max)` som om allt överskott gick till batteriet, men controllern allokerar överskottet till EV-laddare först. När en bil drog t.ex. 5,5 kW av 7 kW överskott gick bara 1,5 kW till batteriet. Fix: för `solar_charge`-slots drar sensorn nu bort `ev_total_power_w` från solöverskottet innan batteriladdning beräknas (`max(0, överskott − ev_total) → batteri`). `ev_total_power_w` (summan av alla laddares `power_w`) läggs till i coordinator-data och exponeras som attribut på laddningssensorn tillsammans med `battery_surplus_w`.

## Nyheter i 0.5.30

- **Fix: Plan executor urladdning visade värde vid min SOC** – när batteriet nådde minimum-SOC (20%) satte controllern korrekt urladdning till 0 W, men plan executor-sensorn rapporterade fortfarande husunderskottet (t.ex. 774 W) eftersom SOC-skydd saknades. Fix: `battery_soc_pct` och `battery_min_soc` sparas nu i coordinator-data; sensorn returnerar 0 W när `battery_soc_pct ≤ battery_min_soc`, vilket speglar controllerns hårda golv.

## Nyheter i 0.5.29

- **Fix: Plan executor urladdning visade bara netto-export, inte totalt batteritappning** – för `export`-slots visade sensorn bara den prisväktade dispatch-komponenten (t.ex. 830 W till nätet) men inte husbehovskomponenten (t.ex. 524 W). Controllern laddade ut summan (1 354 W). Fix: sensorn returnerar nu `min(net_export_w + house_deficit_w, battery_max_w)` för export-slots, där `house_deficit_w = max(0, house_load_w − solar_power_w)`. En ny nyckel `solar_power_w` läggs till i coordinator-data. Attributen exponerar nu `net_export_w`, `house_deficit_w`, `house_load_w` och `solar_power_w` separat för full transparens.

## Nyheter i 0.5.28

- **Fix: Plan executor urladdning visade 0 W vid idle-slots på natten** – sensorn returnerade bara värde för `export`-slots. Vid `idle`-slots på natten (ingen planerad export men batteriet täcker huslast) visade sensorn 0 W trots att controllern laddade ur 1 200–1 500 W. Fix: för `idle`- och `cover_load`-slots returnerar sensorn nu `min(house_load_w − solar_surplus_w, battery_max_w)` — samma husbristslogik som controllern använder. `solar_surplus_w` och `house_load_w` läggs till som sensorattribut. Beteende vid `export`-slots är oförändrat.

## Nyheter i 0.5.27

- **Fix: exportgolv underskattades på soldagar och orsakade batteritömning över natten** – på sommardagar med hög solproduktion fångar `yesterday_consumption_kwh` bara nätuttag (solen täckte resten), vilket ger ett dygnssnitt på ~320 W istället för verklig nattnivå på ~900 W. Exportgolvet beräknades då som 320 W × 9 h + 2 kWh = 4,88 kWh — täckte bara ~4,6 timmar av natten, inte hela den 10+ timmar långa mörka perioden. Efter att ha exporterat ned till golvet nådde batteriet min-SOC kring 00:30 och huset drogs på dyrt nätpris fram till soluppgången. Fix: `_hourly_load_kw` klämms nu till `min(max(yesterday_snitt, house_load_w, 500 W), 1500 W)` — det högsta av dagssnitt och aktuell momentanlast (golv 500 W, tak 1500 W för att utesluta EV-laddningsspikar). Det höjer exportgolvet till ~9–12 kWh på typiska sommarkvällar och förhindrar korrekt export när batteriet inte räcker för hela natten. Samma fix tillämpas i `EnergyPlanner.build_plan()` (ny parameter `house_load_w`) för konsekvens.

## Nyheter i 0.5.26

- **Nytt: Plan executor skugg-sensorer** – två nya sensorer visar vad plan-executorn *hade* skickat till Sonnenbatteriet om den styrde, utan att påverka faktisk styrning. `Plan executor: laddning` ger laddningssetpunkten (W) executorn hade satt — för `solar_charge`-slots cappas den mot faktiskt solöverskott (inte Solcast-prognosen), för `grid_charge`-slots används planens target direkt. `Plan executor: urladdning` ger urladdningssetpunkten för `export`-slots. Båda sensorerna har attribut (`plan_power_w`, `actual_surplus_w`, `capped`) för att jämföra planens estimat mot verkligheten. Använd dessa för att bilda dig en uppfattning om plan-mode-noggrannheten innan du aktiverar plan-driven styrning.

## Nyheter i 0.5.25

- **Fix: EnergyPlanner AVVIKELSE-spam efter 0.5.24** – EnergyPlanner använde fortfarande de gamla entalsvillkoren (`solar_forecast_tomorrow_kwh >= 20 kWh` och `timmar × snittlast`-golv) medan regulatorn redan uppdaterats till slot-baserad logik i 0.5.24. Det gav kontinuerliga avvikelsevärningar (plan=export, faktiskt=idle) varje gång regulatorn korrekt blockerade export för att nettosol imorgon understeg golvet. Båda beräkningarna i planeraren speglar nu regulatorn exakt: (1) exportgolvet är Σ max(0, last_kwh − solar_kwh) slot för slot fram till solar takeover + 2 kWh säkerhetsmarginal; (2) `can_export` kräver `net_solar_tomorrow_kwh >= export_floor_kwh` där nettosol imorgon är Σ max(0, solar_kwh − last_kwh) slot för slot för morgondagens datum.

## Nyheter i 0.5.24

- **Fix: proaktiv export använde råproduktionskontroll och överskattade exportgolvet** – två sammanhängande precisionsförbättringar: (1) Villkoret "kan vi ladda om imorgon?" kontrollerade tidigare råproduktion ≥ 20 kWh, vilket godkändes även när husförbrukningen äter upp det mesta (t.ex. 22 kWh prognos − 13 kWh hus = bara 9 kWh netto, för lite för att fylla batteriet igen). Export tillåts nu bara när **nettosol imorgon** (slot för slot: Σ max(0, solar_kwh − last_kwh)) ≥ exportgolv. (2) Exportgolvet beräknades som `timmar × snittlast + 2 kWh` och behandlade varje timme fram till solar takeover som full 875 W — utan hänsyn till att solen delvis täcker huset under morgon- och kvällsramper. Golvet beräknas nu slot för slot som Σ max(0, last_kwh − solar_kwh), vilket innebär att morgonrampen (05:59–08:59) och kvällsrampen (19:00–20:47) bara bidrar med faktiskt underskott, inte full last — ett mer realistiskt golv (~2 kWh lägre) och mer korrekt exportutrymme.

## Nyheter i 0.5.23

- **Fix: batteri laddade inte från solöverskott under dagtid** – det dynamiska `evening_target_soc` beräknades från *nästa solslot* där sol täcker huslasten (t.ex. 8 minuter bort kl. 11:00), vilket gav `hours_dark = 8 min` och `evening_target_soc ≈ 6%`. Med batteriet vid 20% min-SOC var `battery_soc < evening_target_soc` False → `evening_fill = False` → `prefer_sell = True` → regulatorn exporterade sol istället för att ladda batteriet. Rotorsak: `solar_covers_at`-sökningen startade från `nu` och hittade omedelbart dagens solproduktion, inte morgondagens. Fix: under dagtid (nästa solnedgång är före nästa soluppgång) startar sökningen från **kvällens solnedgång**, så `solar_covers_at` korrekt förankras till morgondagens solar takeover. `hours_dark` beräknas nu som `solar_covers_at − solnedgång` (den faktiska mörka perioden), vilket ger ett realistiskt `evening_target_soc` (~30–50%) och återställer `evening_fill`-logiken under dagtid.

## Nyheter i 0.5.22

- **Fix: batteri töms till minimum-SOC över natten** – proaktiv export körde kontinuerligt från kvällstoppfönstret (19:00–21:00) ända till 04:00 nästa morgon och tömde batteriet till 20% min-SOC. Rotorsak: när det absoluta minimipriset (0,70 kr/kWh) triggade exporten istället för percentilen sänktes `_effective_threshold` till 0,70, vilket fick dispatch-fönstret att inkludera alla nattslots (alla priser ≥ 0,70 kr/kWh). Den prisväktade dispatchen spred sedan batteriets energi över 8–10 billiga nattslots, och golvet krympte allt eftersom soluppgången närmade sig — systemet var alltid ovanför golvet och exporterade kontinuerligt. Fix: dispatch-fönstret (`_high_slots`) använder alltid percentiltröskeln oavsett vad som triggade exporten; och när inga höga prisslots återstår i fönstret men bara absolutminimum är uppfyllt sätts `export_active` till `False`, vilket stoppar all export. Batteriet slutar nu exportera när det genuina höga-pris-fönstret (kväll/morgon) stänger, även om aktuellt säljpris fortfarande är ovanför 0,70 kr/kWh.

## Nyheter i 0.5.21

- **Nytt: EnergyPlanner-sensorer för plan-vs-faktisk-jämförelse** – fyra nya entiteter exponerar aktuell planslott från dagsplaneraren, vilket gör det enkelt att jämföra med faktiska batteribeslut på Lovelace-dashboarden utan att läsa loggar: `Plan: åtgärd` (text: export/solar_charge/idle/…), `Plan: laddningseffekt` (W, speglar laddningssetpunkt), `Plan: urladdningseffekt` (W, speglar urladdningssetpunkt), `Plan: anledning` (text med `battery_soc_est_pct`, `export_floor_kwh` och `evening_target_soc_pct` som attribut).

## Nyheter i 0.5.20

- **Fix: EnergyPlanner falskt-positiva AVVIKELSE vid prefer-sell och proaktiv export** – tre divergensmönster som är förväntade beteenden, inte planeringsbuggar, löses nu som tysta "mjuka matchningar" (DEBUG istället för WARNING): (1) plan=idle men regulatorn laddar — opportunity charging tar över prefer-sell; (2) plan=solar_charge men regulatorn exporterar — prefer-sell låter sol flöda till nätet naturligt medan proaktiv export tömmer återstående batterikudde ovanför golvet; (3) plan=solar_charge men regulatorn är idle — prefer-sell är aktiv, ingen batteriladdning beordrad. Verkliga avvikelser (t.ex. plan=export men regulatorn laddar, eller plan=grid_charge men regulatorn är idle) loggas fortsatt som WARNING.

## Nyheter i 0.5.19

- **Fix: EnergyPlanner AVVIKELSE-spam vid opportunity charging** – planen planerade `idle` för solslots där batteriet redan var ovanför exportgolvet men fortfarande under maxkapacitet. Regulatorn tillämpade sedan opportunity charging (inköpspris under tröskeln) från solöverskott, vilket utlöste en avvikelse-varning var 30:e sekund. Planen planerar nu `solar_charge` när det finns solöverskott *och* utrymme i batteriet under max-SOC, oavsett exportgolvets position. `idle` under soltimmar är reserverat för det fall batteriet är fullt. Detta eliminerar falskt-positiva avvikelsevarna­ingar men behåller korrekt detektering när regulatorn faktiskt avviker.

## Nyheter i 0.5.18

- **Fix: batteri laddades delvis från nätet när sol översteg huslast** – i övergångscykeln när batteriet byter från urladdning (proaktiv export) till laddning rapporterar grid-sensorn fortfarande ett stort exportvärde från föregående cykel. Den beräknade huslasten (`grid + sol − bat_urladdning + bat_laddning − EV`) ger ett negativt resultat som kapas till 0 W, och regulatorn tror att solöverskottet är hela solproduktionen (t.ex. 3 600 W). Batteriet beordrades då ladda med maxkapacitet (t.ex. 3 300 W) trots att faktiskt solöverskott bara var ~2 800 W — vilket drog ~500 W från nätet. Fix: `yesterday_consumption_kwh` hämtas nu innan huslastberäkningen; när sol överstiger 200 W och formeln ger ett värde under gårdagens dygnsmedelsnitt används det snittet som golv, vilket håller överskottsberäkningen realistisk och förhindrar oönskat nätuttag.

## Nyheter i 0.5.17

- **Fix: proaktiv export orsakade nätuttag när huslast översteg sol** – batteriets `discharge_power_setpoint` vid proaktiv export sattes till det prisväktade exportmålet (t.ex. 500 W), men Sonnenbatteriet tolkar detta som *total* batteriuteffekt, inte som nätmatning utöver självförbrukning. När huslasten (t.ex. 988 W) översteg sol (t.ex. 70 W), täckte batteriet 500 W av 488 W-underskottet, och huset importerade resterande ~488 W från nätet – medan den avsedda exporten till nätet var noll. Urladdningsvärdet inkluderar nu husunderskottet: `discharge_w = exportmål + max(0, huslast − sol)`. Batteriet täcker hela husunderskottet och nettoexporten till nätet matchar det planerade exportmålet.

## Nyheter i 0.5.16

- **Fix: kvällsmål nära noll på sommaren** – det dynamiska kvällsmålet använde `predicted_daily_kwh` (temperaturbaserad uppvärmningsmodell) som proxy för total huslast. På sommaren är uppvärmningsbehov noll, vilket gav ~1–2 kWh/dag och ett kvällsmål på ~8% SOC trots att huset drog 1 000 W. Kvällsmålet använder nu `max(predicted_daily_kwh, yesterday_consumption_kwh)`. Sommartid dominerar gårdagens faktiska förbrukning (t.ex. 20 kWh/dag); vintertid kan temperaturmodellen överstiga den. `solar_covers_at`-sökningen använder samma effektiva last, så beräknad mörkertid och energimål är konsistenta med exportgolvet (som redan använde `yesterday_consumption_kwh`).

## Nyligen

- **Fix: kvällsfylling laddade för lite när sol exporterades** – systemet beräknar ett dynamiskt `evening_target_soc` (t.ex. 55 %) baserat på timmar till sol-takeover × huslast. Men villkoret som triggar fyllning krävde att `solar_until_sunset_kwh < battery_remaining_kwh`. Med stark sol (5–9 kW kvar till solnedgång) var det villkoret alltid falskt – systemet antog att framtida sol skulle fylla batteriet. Problemet: den solen exporterades av `prefer_sell` och nådde aldrig batteriet. Dödläget bröts aldrig. Fix: `evening_fill` triggar nu även när `sell_price >= sell_solar_min_price`, dvs. när framtida sol ändå skulle ha exporterats. Batteriet fylls direkt med solöverskottet (t.ex. 5 200 W i ~20 min), sedan återupptas export automatiskt när SOC-målet nås.

- **Fix: proaktiv export ignorerade imorgon bittis högre priser** – exportfönstret klipptes vid `solar_takeover_dt`, vilket osynliggjorde morgonslots med högt pris (t.ex. 07–09 kl till 1,20 kr/kWh mot kvällen till 0,80 kr/kWh). Systemet sålde allt i kväll till lägre pris och hade ingenting kvar till morgontoppen. Fönstergränsen baseras nu på Solcast-solproduktion (`solar_kw < 2 kW`) i stället för klockslag: natt- och tidiga morgonslots inkluderas automatiskt medan middagsslots (full sol) utesluts. Prisväktningen fördelar sedan mer kWh till de dyraste slottarna oavsett om de infaller ikväll eller imorgon bitti.

- **Fix: proaktiv export tömde batteriet för mycket** – exportgolvet (`timmar_mörkt × last + 2 kWh`) beräknades relativt *klockan nu* istället för exportfönstrets start. Under ett 3-4-timmars fönster innebar det att golvet krympte med ~4 kWh (3.5h × 1.05 kW), vilket tyst frigjorde extra headroom som systemet exporterade — och lämnade batteriet nära Sonnenbatteriets 20%-minimum. `hours_dark` är nu förankrat till dagens tidigaste prisslotstart, så golvet är stabilt under hela exportfönstret och krymper bara när fönstret har passerat.

- **Nytt: Energiplan Lovelace-kort** (`www/sem-energy-plan-card.js`) – ett anpassat kort som simulerar batteriet från nu fram till 09:00 nästa morgon. Visar ett canvas-diagram med prisbalkar, batterikurva i kWh och soleffektprofil, plus fastkort för exportfönstret, nattens vilokörning och sol-takeover. Läser livedata från Nordpool, Sonnenbatterie och Solcast. Lägg till i en dashboard med `type: custom:sem-energy-plan-card`. Kopiera `www/sem-energy-plan-card.js` till `/config/www/` i HA-instansen och registrera det som Lovelace-resurs (Dashboard → Resurser → Lägg till → `/local/sem-energy-plan-card.js`).

- **Fix: legionella triggar nu på datum, inte exakt timme** – `due`-kontrollen jämförde exakt antal timmar sedan senaste körning (`days_since >= interval_days`). Om senaste körning var torsdag 14:00 öppnade nästa trigger-fönster torsdag 14:00 en vecka senare; bra tillfällen samma dag (t.ex. sol kl 10:00) missades. Nu jämförs datumet: om idag ≥ förfallodatum är körningen möjlig under hela dagen vid ett lämpligt tillfälle.

- **Fix: solar takeover-observation dubblerades vid omstart** – `_takeover_observed_today` återställdes alltid till `False` vid uppstart. Om HA startades om och solöverskottet tillfälligt var negativt i första cykeln (moln, omstart vid solkanten) loggades en andra observation för samma dag. Storen sparar nu senaste observations-datum; vid laddning återställs `_takeover_observed_today = True` om dagens observation redan är gjord. Bakåtkompatibelt med gamla listformatet.

- **Fix: prisväktad proaktiv export** – urladdningseffekten vid proaktiv export beräknades tidigare som `exporterbart / återstående_timmar`, vilket gav samma W oavsett pris. Nu viktas varje 15-minutersslot mot summan av alla kvarvarande höga priser: `W = exporterbart × (aktuellt_pris / prissumma) / 0,25 h`. Resultatet är att mer energi säljs vid höga priser och mindre vid låga, utan att den totala exporterade energin förändras.

- **Stöd för officiell Nordpool-integration** – utöver HACS-varianten (`custom_components/nordpool`) kan nu den officiella HA Nordpool-integrationen användas. Välj integrationstyp och prisområde (t.ex. `SE3`) under Nät & Prissättning. Priser hämtas via `nordpool.get_prices_for_date`-servicen, konverteras från SEK/MWh till SEK/kWh och cachar per dag. All schemaläggarlogik (proaktiv export, opportunistisk laddning, morgonexport) fungerar identiskt oavsett källan.
- **Morgonexport – behovsbaserad logik** – export ur batteriet på morgonen för att ge plats åt inkommande sol utlöses nu baserat på faktiskt behov i stället för fast tidsgräns. Logiken kontrollerar: (1) förväntat solöverskott > tillgängligt batteriutrymme, (2) aktuellt säljpris > produktionsviktat solsnitt, (3) batteriet är inte tomt. Förhindrar felaktig export på eftermiddagen och export vid låga priser.
- **Fix: `negative_slots_ahead` använde säljpris** – prisjämförelsen för negativa slots framåt använde `sell_sek` i stället för `spot_sek`, vilket innebar att slots med svagt negativt spotpris men positivt säljpris inte räknades. Fixat till att använda `spot_sek` genomgående.
- **Fix: batteri laddade och urladdade simultant** – solöverskottsladdning startade även när urladdning redan var beslutad (t.ex. proaktiv export). Guard tillagd: batteriet laddas från solöverskott bara om ingen urladdning är kommenderad samma cykel.

## Nyheter i 0.5.11

- **Fix: stabilt export-golv baserat på gårdagens förbrukning** – golvets timberäkning (`timmar_mörkt × huslast + 2 kWh`) använde tidigare den momentana huslasten, vilket innebar att ett värmepumpsstart kl 04:00 kunde mångdubbla golvet och blockera export i onödan. Huslasten ersätts nu med gårdagens dygnsmedelförbrukning (`last_period`-attributet från `yesterday_consumption_entity` delat på 24 h). Golvet varierar nu bara när Solcast-prognosen förändras, inte vid momentana toppar.
- **Fix: solar_takeover_dt sparas undan mot inaktuell Solcast-data** – om Solcast-sensorn serverade gammal data (alla slots i det förflutna) föll koden tillbaka på nästa soluppgång (~22 h), vilket gav ett felaktigt golv på 15–20 kWh och blockerade export. Koordinatorn sparar nu senaste giltiga `solar_takeover_dt` och återanvänder det om Solcast tillfälligt inte kan beräkna ett nytt värde. Värdet nollställs när solen producerar igen (> 200 W). Om Solcast uppdateras och ger ett *tidigare* takeover-datum uppdateras det sparade värdet.

## Nyheter i 0.5.10

- **Fix: solar_takeover söker dagens prognos först** – exportgolvet beräknades mot imorgons Solcast-prognos även när solen redan producerade idag, vilket gav ett felaktigt "mörker" på 20+ timmar och blockerade export i onödan. Koden söker nu i dagens detailedForecast-slots (framtida slots) innan den faller tillbaka på imorgons prognos.
- **Fix: proaktiv export blockeras vid solöverskott** – batteriet laddade ur för export samtidigt som det laddades från solöverskott. Proaktiv batteriexport aktiveras nu bara när solproduktionen inte överstiger huslasten med mer än 200 W. Vid solöverskott exporteras solenergin naturligt utan batteriinblandning.
- **Fix: state_class för monetary- och energy-sensorer** – ett antal sensorer använde `state_class = MEASUREMENT` tillsammans med `device_class = MONETARY` eller `ENERGY`, vilket HA varnar för. `BatteryAccumulatedCostSensor` använder nu `TOTAL`; övriga prognos- och beräkningssensorer använder `None`.

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
En av dessa Nordpool-integrationer måste vara installerad:
- **nordpool (HACS)** – `custom_components/nordpool`, ger `raw_today`/`raw_tomorrow` med 15-minutspriser
- **Nord Pool (officiell)** – inbyggd HA-integration (HA 2024.x+), kräver att `nordpool_type = official` och `nordpool_area` (t.ex. `SE3`) anges i konfigurationen

Valfritt men rekommenderat:
- **solcast_solar** – solprognos

### Konfigurationsflöde

Konfigurationen sker i sex steg:

**Steg 1 – Nät & Prissättning**
- Nordpool-sensor (obligatorisk) – spotprissensor från vald integration
- Nordpool-integrationstyp – `hacs` (standard) eller `official`
- Nordpool prisområde – t.ex. `SE3` (används bara vid `official`)
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
