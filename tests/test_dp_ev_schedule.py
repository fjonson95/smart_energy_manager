"""Bilens nätladdning i plan B (fas B5.2).

15 nov 2026 02–06:45 laddade Amina 8 A på L1 ("billig timme", C05) medan plan B
planerade 7,6–7,8 kW batteriladdning; fasskyddet ströp batteriet till 5,3 kW
(11 kWh). Plan B ska schemalägga bilen och se den i fasgränsen.
"""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.dp_planner import DPPlanner
from custom_components.smart_energy_manager.energy_controller import (
    CarConfig,
    ChargerConfig,
    ChargerState,
    EnergyController,
    EnergyState,
)
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule,
    PriceScheduler,
    PriceSlot,
)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 14, 22, 0, tzinfo=TZ)
DEADLINE = datetime(2026, 11, 15, 7, 0, tzinfo=TZ)
EV_KW = 8 * 230 / 1000.0  # 8 A, 1 fas


def _spot(start):
    if 1 <= start.hour < 5:
        return 0.10
    return 1.60 if 7 <= start.hour < 21 else 0.60


def _schedule():
    sch = PriceScheduler()
    slots = []
    for q in range(26 * 4):
        start = NOW + timedelta(minutes=15 * q)
        spot = _spot(start)
        slots.append(PriceSlot(start=start, end=start + timedelta(minutes=15), spot_sek=spot,
                               buy_sek=sch._buy_price(spot), sell_sek=sch._sell_price(spot)))
    return PriceSchedule(slots=slots)


def _plan(ev_kwh=None):
    demand = None if ev_kwh is None else {
        "energy_kwh": ev_kwh, "deadline": DEADLINE, "power_kw": EV_KW, "phases": ("L1",),
    }
    return DPPlanner(battery_min_soc=8, battery_max_soc=99).build_plan(
        now=NOW, battery_soc_pct=13, battery_capacity_kwh=30.69, battery_max_power_kw=8,
        ps=_schedule(), predicted_daily_kwh=28.8, house_load_w=1200, phase_limit_a=15.0,
        ev_demand=demand,
    )


def _ev_kwh(plan):
    return sum(s.ev_charge_w / 1000.0 * 0.25 for s in plan.slots)


def test_without_ev_plan_has_no_ev_schedule():
    assert _ev_kwh(_plan()) == 0.0


def test_ev_avoids_the_battery_charging_quarters_when_there_is_room():
    plan = _plan(ev_kwh=2.0)
    ev = [s for s in plan.slots if s.ev_charge_w > 0]
    assert abs(_ev_kwh(plan) - 2.0) < 0.01
    assert all(s.start < DEADLINE for s in ev)
    assert all(s.target_power_w <= 0 for s in ev)


def test_ev_that_needs_the_whole_night_lowers_the_battery_charge_cap():
    # C05 utan känt behov: laddar alla kvartar fram till 07:00 (9 h × 1,84 kW).
    plan = _plan(ev_kwh=EV_KW * 9)
    shared = [s for s in plan.slots if s.ev_charge_w > 0 and s.target_power_w > 0]
    assert shared
    # L1: 15 A × 230 V − 0,4 kW husets andel − 1,84 kW bil ≈ 1,21 kW → 3 × 1,21 ≈ 3,6 kW.
    assert max(s.target_power_w for s in shared) <= 3700
    # Utan bilen laddar plan B full effekt i samma kvartar.
    base = {s.start: s.target_power_w for s in _plan().slots}
    assert max(base[s.start] for s in shared) > 7000


def _night_state(**kwargs):
    t = datetime(2026, 11, 15, 2, 0, tzinfo=TZ)
    slots = [PriceSlot(start=t + timedelta(minutes=15 * i), end=t + timedelta(minutes=15 * (i + 1)),
                       spot_sek=0.1 if i < 8 else 1.6, buy_sek=1.1 if i < 8 else 2.9,
                       sell_sek=0.2 if i < 8 else 1.7) for i in range(48)]
    cfg = ChargerConfig(name="Amina", charger_switch="switch.amina", charger_current="number.amina",
                        cars=[CarConfig(name="Kia", car_phases=1)])
    charger = ChargerState(config=cfg, connected=True, active_car_name="Kia", soc_pct=50.0)
    return EnergyState(
        house_load_w=1200.0, battery_soc_pct=40.0, battery_capacity_kwh=30.69, battery_max_power_kw=8.0,
        grid_power_l1=400.0, grid_power_l2=400.0, grid_power_l3=400.0, chargers=[charger],
        spot_price_sek_kwh=0.1, buy_price_sek_kwh=1.1, sell_price_sek_kwh=0.2,
        solar_forecast_today_kwh=2.0, solar_forecast_tomorrow_kwh=2.0,
        price_schedule=PriceSchedule(slots=slots), now=t, **kwargs,
    )


def _ctrl():
    return EnergyController(max_current_per_phase=17, battery_min_soc=8, battery_max_soc=99)


def test_cheap_hour_without_plan_slot_does_not_charge_ev():
    # "Billig timme" (C05) togs bort i B5.4.1 – bara planens kvartar laddar från nätet.
    decision = _ctrl().compute(_night_state())
    assert not decision.charger_decisions[0].enable


def test_plan_ev_slot_charges_at_8_a():
    decision = _ctrl().compute(_night_state(plan_ev_charge_w=EV_KW * 1000.0, plan_ev_charger_name="Amina"))
    d = decision.charger_decisions[0]
    assert d.enable and round(d.current_a) == 8
    assert "plan" in d.reason
