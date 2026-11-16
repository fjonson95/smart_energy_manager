"""Plan B:s etikett för små solöverskott (12 nov 2026).

10:30–11:30 visade kortet "Nätladdning" 434 W med Solcast p50 2 kW och köp
2,90 kr: DP:s minsta energisteg (0,1 kWh) var större än solöverskottet, så
kvarten märktes grid_charge och exekutorn köpte nätdelen.
"""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.dp_planner import DPPlanner
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule,
    PriceScheduler,
    PriceSlot,
)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 12, 4, 0, tzinfo=TZ)
SUN = NOW + timedelta(hours=6, minutes=30)  # 10:30


def _plan(solar_kw: float):
    sch = PriceScheduler()
    slots = []
    for q in range(24 * 4):
        start = NOW + timedelta(minutes=15 * q)
        slot = PriceSlot(start=start, end=start + timedelta(minutes=15), spot_sek=0.30,
                         buy_sek=sch._buy_price(0.30), sell_sek=sch._sell_price(0.30))
        if start == SUN:
            slot.solar_kw = solar_kw
            slot.solar_kwh = solar_kw * 0.25
        slots.append(slot)
    return DPPlanner(battery_min_soc=8, battery_max_soc=99).build_plan(
        now=NOW, battery_soc_pct=8, battery_capacity_kwh=30.69, battery_max_power_kw=8,
        ps=PriceSchedule(slots=slots), predicted_daily_kwh=28.8, house_load_w=1200,
    )


def _sun_slot(plan):
    return next(s for s in plan.slots if s.start == SUN)


def test_step_mostly_from_sun_is_solar_charge():
    # Huset ~1,2 kW, solen 1,44 kW → 0,06 kWh över; steget är ~0,104 kWh AC (58 % sol).
    slot = _sun_slot(_plan(solar_kw=1.44))
    assert slot.target_power_w > 0
    assert slot.action == "solar_charge"
    assert "% sol" in slot.reason


def test_grid_charge_in_economic_peak_stores_real_solar_surplus():
    # 12 nov 10:26: sol 3,8 kW, hus 1,3 kW, köp 2,93 ≥ 1,2 × nattens 2,0 (economic_peak);
    # förr täckte grenen bara underskott och 2,5 kW exporterades.
    from custom_components.smart_energy_manager.energy_controller import (
        ControlDecision, EnergyController, EnergyState,
    )
    from custom_components.smart_energy_manager.energy_planner import DayPlan, PlannedSlot

    now = datetime(2026, 11, 12, 10, 26, tzinfo=TZ)
    q = now.replace(minute=15)
    cur = PriceSlot(start=q, end=q + timedelta(minutes=15), spot_sek=1.6, buy_sek=2.93, sell_sek=1.66)
    night = PriceSlot(start=q + timedelta(hours=10), end=q + timedelta(hours=10, minutes=15),
                      spot_sek=0.85, buy_sek=2.0, sell_sek=0.9)
    ps = PriceSchedule(slots=[cur, night], best_charge_slot=night)
    state = EnergyState(
        solar_power_w=3800.0, house_load_w=1300.0, battery_soc_pct=9.0,
        battery_capacity_kwh=30.69, battery_max_power_kw=8.0,
        grid_power_l1=-1050.0, grid_power_l2=-530.0, grid_power_l3=-870.0,
        spot_price_sek_kwh=1.6, buy_price_sek_kwh=2.93, sell_price_sek_kwh=1.66,
        price_schedule=ps, now=now,
    )
    slot = PlannedSlot(start=q, end=q + timedelta(minutes=15), action="grid_charge",
                       target_power_w=434.0, battery_soc_est_pct=9.0, reason="test")
    plan = DayPlan(generated_at=now, valid_until=now + timedelta(hours=1), evening_target_soc_pct=8.0,
                   export_floor_kwh=2.46, total_exportable_kwh=0.0, expected_revenue_sek=0.0,
                   solar_takeover_dt=None, hourly_load_kw=1.58, slots=[slot])
    ctrl = EnergyController(max_current_per_phase=17, battery_min_soc=8, battery_max_soc=99)
    out = ctrl.apply_plan_executor(plan, "auto", state, ControlDecision(), now, 2500.0)
    assert out.battery_charge_power_w > 2000.0
    assert "economic_peak" in out.reason


def test_dp_charge_slot_holds_battery_and_carries_grid_part():
    slot = _sun_slot(_plan(solar_kw=1.44))
    assert slot.action == "solar_charge"
    assert slot.hold_battery
    assert 0.0 < slot.grid_charge_w < 0.5 * slot.target_power_w


def _solar_charge_run(solar_w, house_w, grid_charge_w, hold_battery, v_charge=2.5):
    # 15 nov 2026 12:00: plan B 876 W, 70 % sol, köp 1,53 kr – solen gav ~1,3 kW mot
    # husets ~1,6 kW och exekutorn laddade ur i stället för att ladda.
    from custom_components.smart_energy_manager.energy_controller import (
        ControlDecision, EnergyController, EnergyState,
    )
    from custom_components.smart_energy_manager.energy_planner import DayPlan, PlannedSlot

    now = datetime(2026, 11, 15, 12, 5, tzinfo=TZ)
    q = now.replace(minute=0)
    cur = PriceSlot(start=q, end=q + timedelta(minutes=15), spot_sek=0.47, buy_sek=1.53, sell_sek=0.54)
    ps = PriceSchedule(slots=[cur], best_charge_slot=cur)
    state = EnergyState(
        solar_power_w=solar_w, house_load_w=house_w, battery_soc_pct=55.0,
        battery_capacity_kwh=30.69, battery_max_power_kw=8.0,
        spot_price_sek_kwh=0.47, buy_price_sek_kwh=1.53, sell_price_sek_kwh=0.54,
        price_schedule=ps, now=now,
    )
    slot = PlannedSlot(start=q, end=q + timedelta(minutes=15), action="solar_charge",
                       target_power_w=876.0, battery_soc_est_pct=55.0, reason="test",
                       hold_battery=hold_battery, grid_charge_w=grid_charge_w)
    plan = DayPlan(generated_at=now, valid_until=now + timedelta(hours=1), evening_target_soc_pct=8.0,
                   export_floor_kwh=2.46, total_exportable_kwh=0.0, expected_revenue_sek=0.0,
                   solar_takeover_dt=None, hourly_load_kw=1.58, slots=[slot],
                   marginal_value_charge_sek_kwh=v_charge)
    ctrl = EnergyController(max_current_per_phase=17, battery_min_soc=8, battery_max_soc=99)
    return ctrl.apply_plan_executor(plan, "auto", state, ControlDecision(), now,
                                    max(0.0, solar_w - house_w))


def test_dp_solar_charge_without_sun_buys_grid_part_instead_of_discharging():
    out = _solar_charge_run(1300.0, 1600.0, grid_charge_w=263.0, hold_battery=True)
    assert out.battery_discharge_power_w == 0.0
    assert out.battery_charge_power_w == 263.0
    assert "nätdelen" in out.reason


def test_dp_solar_charge_with_more_sun_stores_surplus_plus_grid_part():
    out = _solar_charge_run(2600.0, 1600.0, grid_charge_w=263.0, hold_battery=True)
    assert out.battery_charge_power_w == 1263.0


def test_dp_solar_charge_without_sun_self_consumes_when_buy_exceeds_stored_value():
    # Backtest utan facit 27 okt–10 nov: nätdelen köptes för 2,2–3,7 kr och kostade 3,7 kr.
    out = _solar_charge_run(1300.0, 1600.0, grid_charge_w=263.0, hold_battery=True, v_charge=1.40)
    assert out.battery_charge_power_w == 0.0
    assert out.battery_discharge_power_w == 300.0


def test_v_solar_charge_deficit_still_self_consumes():
    # 2026-09-11: V-planens solar_charge utan sol ska fortfarande täcka huset ur batteriet.
    out = _solar_charge_run(1300.0, 1600.0, grid_charge_w=0.0, hold_battery=False)
    assert out.battery_charge_power_w == 0.0
    assert out.battery_discharge_power_w == 300.0


def test_step_mostly_from_grid_is_still_grid_charge():
    # 0,02 kWh över → under hälften av steget är sol.
    slot = _sun_slot(_plan(solar_kw=1.28))
    if slot.target_power_w > 0:
        assert slot.action == "grid_charge"
