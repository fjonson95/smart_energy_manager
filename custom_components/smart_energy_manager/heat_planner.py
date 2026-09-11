"""Värmestyrning för Smart Energy Manager (v1.0 steg 7 – huset som värmelager).

Egen, ny fil skild från energy_planner.py (batteriet) – annan fysisk
domän (uppvärmning, inte batteri) som väntas växa med egen logik i takt
med att steg 7:s punkter implementeras.

Status (docs/v1_implementation_plan.md, "STEG 7"): punkt 2 (håll
elpatronerna utanför), 4 (lönsamhetsregel med COP), 5 (soldrift via
pannans egen väg) och 6 (dump/desinficering som schemalagda laster)
implementerade som fristående, testade funktioner – INGEN koppling till
skarp styrning (coordinator.py/energy_controller.py/legionella.py
orörda). Punkt 1 och 3 (rumsvis termisk regression/reglering) parkerade
tills en vintersäsongs mätdata finns – rumstemperaturgivarna har bara
haft data sedan ~2026-07-25, inte sedan oktober 2025 som ursprungligen
antaget.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional


def is_preheat_profitable(
    price_peak_sek_kwh: float,
    price_preheat_sek_kwh: float,
    cop_preheat: float,
    cop_normal: float,
) -> bool:
    """v1.0 steg 7, punkt 4: lönsamhetsregel för förvärmning av huset/
    ackumulatortanken.

    Förvärmning (köra värmepumpen extra nu, vid ett billigare pris, för
    att slippa köra lika mycket vid en kommande, dyrare pristopp – husets/
    tankens egen värmetröghet gör förskjutningen möjlig) kostar extra
    eftersom verkningsgraden (COP) sjunker vid högre
    framledningstemperatur: att lagra värme för senare kräver en högre
    temperatur än vad komforten just nu kräver.

    Planens formel (docs/v1_implementation_plan.md, STEG 7 punkt 4):
        vinst   = (pris_topp − pris_förvärm) × kWh_förskjuten
        kostnad = kWh_förskjuten × (1/cop_förvärm − 1/cop_normal) × pris_förvärm

    kWh_förskjuten (hur många kWh värme som faktiskt flyttas i tid) stryks
    ur jämförelsen – den är samma på båda sidor (så länge > 0), så
    lönsamheten avgörs helt av pris- och COP-förhållandet, inte av hur
    mycket som flyttas:

        pris_topp − pris_förvärm > (1/cop_förvärm − 1/cop_normal) × pris_förvärm

    `cop_preheat`/`cop_normal` är MEDVETET indataparametrar, inte en
    utläsning av `sensor.vp_verkningsgrad` här inne. Den rå sensorn
    (COP × 100, från `boiler_hppower`/`ivt_total_active_power`) är
    starkt brusig kring kompressorstart/avfrostning – uppmätta toppar
    över 10 000 % under en enda vecka i skarp drift (2026-09-09) – och
    det finns ännu ingen kalibrerad modell för hur COP faktiskt beror av
    framledningstemperaturen (`number.boiler_tempparmode`); det kräver
    egna mätningar under en förvärmningsperiod, inte gissad fysik.
    Anroparen ansvarar för ett rimligt, filtrerat COP-värde (t.ex. ett
    glidande medel över en stabil körningsperiod), inte den råa
    momentanavläsningen.

    Aldrig lönsamt (returnerar False) om `cop_preheat` eller `cop_normal`
    är <= 0 (odefinierat, t.ex. värmepumpen av) – förvärmning kan då inte
    bedömas överhuvudtaget.
    """
    if cop_preheat <= 0.0 or cop_normal <= 0.0:
        return False
    cost_per_kwh_shifted = (1.0 / cop_preheat - 1.0 / cop_normal) * price_preheat_sek_kwh
    profit_per_kwh_shifted = price_peak_sek_kwh - price_preheat_sek_kwh
    return profit_per_kwh_shifted > cost_per_kwh_shifted


def elpatron_avoidance_setpoints(
    buy_price_sek_kwh: float,
    expensive_price_threshold_sek_kwh: float,
    normal_tempparmode_c: float = 10.0,
    avoidance_tempparmode_c: float = -5.0,
    normal_auxheaterdelay_kmin: float = 300.0,
    avoidance_auxheaterdelay_kmin: float = 600.0,
) -> tuple[float, float]:
    """v1.0 steg 7, punkt 2: håll elpatronerna (Eltillskott) utanför genom
    att bara nudga två av pannans EGNA trösklar under dyra slots -
    kompressorn får jobba ikapp längre innan pannan själv griper in med
    elpatron, istället för att SEM tar över styrningen av elpatronen.

    Planens förutsättning för allt annat i steg 7 ("Håll elpatronerna
    utanför"): `number.boiler_tempparmode` (parallellförskjutning av
    värmekurvan, idag 10 °C) sänks mot 0…−5 °C och
    `number.boiler_auxheaterdelay` (K·min - ackumulerat temperaturunderskott
    över tid innan elpatronen tillåts starta) förlängs, under dyra slots.
    En lägre tempparmode sänker pannans egen framledningsmål, vilket i
    praktiken höjder tröskeln för när pannan tycker sig ligga så långt
    efter att elpatron behövs; ett större auxheaterdelay kräver att
    underskottet ackumuleras längre innan elpatron tillåts starta.

    Returnerar (tempparmode_c, auxheaterdelay_kmin) - de två EGNA
    pannovärdena att skriva, inte en egen ersättningsstyrning. Använd
    helpern `binary_sensor.eltillskott_aktivt` som facit vid intrimning av
    default-värdena för avoidance-läget (verifierat live 2026-09-11: normal
    10 °C/300 K·min, `eltillskott_aktivt=off`) - siffrorna ovan är en
    rimlig startpunkt inom `tempparmode`s -126…126 °C och
    `auxheaterdelay`s 10…1000 K·min-spann, inte en kalibrerad slutgiltig
    modell.

    Ren tröskelfunktion (inte en glidande skala mot prisets storlek) - matchar
    hur `is_preheat_profitable` och `should_dump_to_hot_water` redan är
    byggda: en klar av/på-gräns är lättare att verifiera och trimma mot
    `eltillskott_aktivt` än en kontinuerlig kurva utan mätdata att kalibrera
    den mot.
    """
    if buy_price_sek_kwh >= expensive_price_threshold_sek_kwh:
        return avoidance_tempparmode_c, avoidance_auxheaterdelay_kmin
    return normal_tempparmode_c, normal_auxheaterdelay_kmin


def solar_compressor_boost_kw(
    surplus_available_for_heat_w: float,
    max_comp_power_kw: float = 25.0,
) -> float:
    """v1.0 steg 7, punkt 5: soldrift via pannans egen väg.

    `number.boiler_pvmaxcomp` (0-25 kW, står på 0 idag) är kompressorns
    maxeffekt vid PV-överskott - pannans inbyggda soldriftläge. Istället för
    att SEM självt orkestrerar värmepumpens effekt mot solöverskottet (som
    med batteri/EV) sätter den här funktionen bara ett TAK som pannan sedan
    själv reglerar kompressorn inom - en enkel, direkt kW-omvandling av det
    överskott som redan finns, inget eget beslut om HUR mycket kompressorn
    faktiskt ska köra just nu.

    `surplus_available_for_heat_w` ska vara överskottet som blir kvar EFTER
    SEM:s egen prioritetsordning (CLAUDE.md: 1. huslast, 2. EV, 3. batteri) -
    samma "remaining_surplus" som redan når fram till steg 4:s extra
    varmvatten-prioritet i `EnergyController.compute()`, inte rått
    `solar_surplus_w` innan EV/batteri fått sitt. Annars konkurrerar
    pannans egen soldrift med SEM:s egna prioriteringar om samma överskott.

    Klämd mot pannans egna gränser (0-25 kW som default, matchar
    `number.boiler_pvmaxcomp`s min/max) - ett negativt eller orimligt stort
    överskott kan aldrig ge ett värde utanför det pannan faktiskt tillåter.
    """
    return max(0.0, min(surplus_available_for_heat_w / 1000.0, max_comp_power_kw))


def should_dump_to_hot_water(
    marginal_value_sek_kwh: float,
    sell_price_sek_kwh: float,
    hot_water_temp_c: Optional[float],
    min_temp_c: float,
) -> bool:
    """v1.0 steg 7, punkt 6 (första halvan): dumpa solöverskott till
    varmvatten istället för att sälja det, när batteriet ändå inte vill
    ha det.

    Planens formulering: "Dumpa till varmvatten när V < sälj_nu – samma
    jämförelse som avgör om batteriet ska laddas." `V > sälj_nu` är exakt
    energy_planner.py::build_plan()s regel 1-villkor för att LADDA
    batteriet från solöverskott (se `V_charge > slot.sell_sek` i
    build_plan()); `V < sälj_nu` är alltså regel 1:s ELSE-gren – tillfällen
    då batteriet hellre säljer överskottet direkt än sparar det. Den här
    funktionen lägger till ett tredje alternativ i just den grenen: dumpa
    till varmvatten istället för att sälja, eftersom självkonsumtion
    (undviker ett framtida köp) nästan alltid slår försäljning i den här
    tariffen (köp ≥ 1,17 kr högre än sälj, se steg 1:s brytpunktsformel,
    CLAUDE.md "Vad vi medvetet inte bygger").

    Temperaturspärren speglar EnergyController._can_start_extra_hot_water()
    exakt (samma hysteresis mot korta cykler): dumpa bara om tanken är
    UNDER `min_temp_c`, inte upp till `max_temp_c` – tanken räknas som
    "redan varm nog" redan vid min_temp, inte först vid max_temp. Ingen
    sensor konfigurerad (`hot_water_temp_c=None`) tillåter alltid dump,
    matchar samma "ingen sensor → tillåt" som _can_start_extra_hot_water().
    """
    if hot_water_temp_c is not None and hot_water_temp_c >= min_temp_c:
        return False
    return marginal_value_sek_kwh < sell_price_sek_kwh


def schedule_cheapest_window(
    candidate_slots: list[tuple[datetime, datetime, float]],
    deadline: datetime,
    duration_minutes: float,
) -> Optional[datetime]:
    """v1.0 steg 7, punkt 6 (andra halvan): hitta starten av det billigaste
    SAMMANHÄNGANDE tidsfönstret av minst `duration_minutes` för en bunden
    last med deadline (t.ex. legionella-desinficering, ~60 min – pannan kör
    ett obrutet program, inte utspridda kvartar).

    `candidate_slots`: lista av (start, end, köppris) – t.ex. byggd direkt
    från `PriceSchedule.slots`. Förväntas jämnstora och sammanhängande
    (matchar hur riktiga prisslots faktiskt ser ut, kvartstimmar utan
    luckor); ett oregelbundet indata ger inget fel men inget meningsfullt
    svar heller.

    Generisk merit-order-schemaläggning, samma PRINCIP som EV-
    schemaläggningen i energy_planner.py (steg 6) – men EV:s mekanism
    (fyll behovet från de billigaste SLOTTEN var för sig, oavsett var i
    tiden de ligger) passar inte här eftersom desinficeringen kräver ett
    obrutet fönster, inte utspridda kvartar. Därför en egen, enklare
    glidande-medel-funktion istället för att återanvända EV:s.

    Returnerar None om inga kandidatslots finns före deadline. Om
    `duration_minutes` inte får plats i det som finns kvar (färre slots än
    behövs) används hela den återstående tiden istället för att ge upp –
    bättre en kortare körning än ingen alls, samma princip som
    `legionella.py`s nödstart vid överskriden frist.
    """
    eligible = [s for s in candidate_slots if s[1] <= deadline]
    if not eligible:
        return None
    eligible.sort(key=lambda s: s[0])
    slot_h = (eligible[0][1] - eligible[0][0]).total_seconds() / 3600.0
    if slot_h <= 0:
        return None
    slots_needed = max(1, math.ceil((duration_minutes / 60.0) / slot_h))
    slots_needed = min(slots_needed, len(eligible))

    best_start: Optional[datetime] = None
    best_avg_price = float("inf")
    for i in range(0, len(eligible) - slots_needed + 1):
        window = eligible[i:i + slots_needed]
        avg_price = sum(s[2] for s in window) / slots_needed
        if avg_price < best_avg_price:
            best_avg_price = avg_price
            best_start = window[0][0]
    return best_start
