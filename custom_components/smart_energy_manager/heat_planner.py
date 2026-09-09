"""Värmestyrning för Smart Energy Manager (v1.0 steg 7 – huset som värmelager).

Egen, ny fil skild från energy_planner.py (batteriet) – annan fysisk
domän (uppvärmning, inte batteri) som väntas växa med egen logik i takt
med att steg 7:s punkter implementeras.

Status (docs/v1_implementation_plan.md, "STEG 7"): punkt 4 (lönsamhetsregel
med COP) och 6 (dump/desinficering som schemalagda laster) implementerade
som fristående, testade funktioner – INGEN koppling till skarp styrning
(coordinator.py/energy_controller.py/legionella.py orörda). Punkt 1 och 3
(rumsvis termisk regression/reglering) parkerade tills en vintersäsongs
mätdata finns – rumstemperaturgivarna har bara haft data sedan
~2026-07-25, inte sedan oktober 2025 som ursprungligen antaget. Punkt 2
(håll elpatronerna utanför) och 5 (soldrift via pannans egen väg) inte
påbörjade.
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
