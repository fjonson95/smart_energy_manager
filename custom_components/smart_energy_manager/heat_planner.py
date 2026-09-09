"""Värmestyrning för Smart Energy Manager (v1.0 steg 7 – huset som värmelager).

Egen, ny fil skild från energy_planner.py (batteriet) – annan fysisk
domän (uppvärmning, inte batteri) som väntas växa med egen logik i takt
med att steg 7:s punkter implementeras.

Status (docs/v1_implementation_plan.md, "STEG 7"): bara punkt 4
(lönsamhetsregel med COP) implementerad. Punkt 1 och 3 (rumsvis
termisk regression/reglering) parkerade tills en vintersäsongs mätdata
finns – rumstemperaturgivarna har bara haft data sedan ~2026-07-25,
inte sedan oktober 2025 som ursprungligen antaget. Punkt 2 (håll
elpatronerna utanför), 5 (soldrift via pannans egen väg) och 6 (dump/
desinficering som schemalagda laster) inte påbörjade.
"""
from __future__ import annotations


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
