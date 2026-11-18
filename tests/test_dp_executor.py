"""Exekutorn för plan B klämmer bara mot fysiken (fas B5.3, HANDOFF.md 4.6).

econ_peak (köp ≥ 1,2 × billigaste 12 h) stoppade planerad nätladdning och
prefer_sell (sälj ≥ 0,80 kr och SOC över kvällsmålet) exporterade överskott –
båda beslut som plan B redan hade fattat.
"""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.energy_controller import (
    ControlDecision,
    EnergyController,
    EnergyState,
)
from custom_components.smart_energy_manager.energy_planner import DayPlan, PlannedSlot
from custom_components.smart_energy_manager.price_scheduler import PriceSchedule, PriceSlot

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 12, 10, 26, tzinfo=TZ)
Q = NOW.replace(minute=15)


def _run(action, target_w, *, solar_w, house_w, buy=2.93, sell=1.66, soc=50.0, v_charge=2.5,
         hold=False, grid_charge_w=0.0):
    cur = PriceSlot(start=Q, end=Q + timedelta(minutes=15), spot_sek=sell - 0.065, buy_sek=buy, sell_sek=sell)
    night = PriceSlot(start=Q + timedelta(hours=10), end=Q + timedelta(hours=10, minutes=15),
                      spot_sek=0.85, buy_sek=2.0, sell_sek=0.9)
    state = EnergyState(
        solar_power_w=solar_w, house_load_w=house_w, battery_soc_pct=soc,
        battery_capacity_kwh=30.69, battery_max_power_kw=8.0,
        spot_price_sek_kwh=sell - 0.065, buy_price_sek_kwh=buy, sell_price_sek_kwh=sell,
        price_schedule=PriceSchedule(slots=[cur, night], best_charge_slot=night), now=NOW,
    )
    slot = PlannedSlot(start=Q, end=Q + timedelta(minutes=15), action=action, target_power_w=target_w,
                       battery_soc_est_pct=soc, reason="test", hold_battery=hold, grid_charge_w=grid_charge_w)
    plan = DayPlan(generated_at=NOW, valid_until=NOW + timedelta(hours=1), evening_target_soc_pct=8.0,
                   export_floor_kwh=2.46, total_exportable_kwh=0.0, expected_revenue_sek=0.0,
                   solar_takeover_dt=None, hourly_load_kw=1.5, slots=[slot],
                   marginal_value_charge_sek_kwh=v_charge)
    ctrl = EnergyController(max_current_per_phase=25, battery_min_soc=8, battery_max_soc=99)
    return ctrl.apply_plan_executor(plan, "auto", state, ControlDecision(), NOW,
                                    max(0.0, solar_w - house_w))


def test_grid_charge_is_not_vetoed_by_economic_peak():
    # köp 2,93 ≥ 1,2 × 2,0: den borttagna V-vägen laddade ur, planen laddar som planerat.
    out = _run("grid_charge", 3000.0, solar_w=0.0, house_w=1300.0)
    assert out.battery_charge_power_w == 3000.0
    assert out.battery_discharge_power_w == 0.0


def test_grid_charge_adds_real_solar_surplus():
    out = _run("grid_charge", 3000.0, solar_w=2300.0, house_w=1300.0)
    assert out.battery_charge_power_w == 4000.0


def test_cover_load_stores_unplanned_surplus_below_stored_value():
    # sälj 1,66 ≥ 0,80 och SOC 50 % över kvällsmålet: prefer_sell hade exporterat.
    out = _run("cover_load", -1000.0, solar_w=3800.0, house_w=1300.0, v_charge=2.5)
    assert out.battery_charge_power_w == 2500.0


def test_cover_load_sells_unplanned_surplus_above_stored_value():
    out = _run("cover_load", -1000.0, solar_w=3800.0, house_w=1300.0, v_charge=1.2)
    assert out.battery_charge_power_w == 0.0
    assert out.battery_discharge_power_w == 0.0


def test_cover_load_covers_real_deficit():
    out = _run("cover_load", -1000.0, solar_w=200.0, house_w=1700.0)
    assert out.battery_discharge_power_w == 1500.0


def test_idle_hold_battery_never_discharges():
    out = _run("idle", 0.0, solar_w=0.0, house_w=1500.0, hold=True)
    assert out.battery_discharge_power_w == 0.0
    assert out.battery_charge_power_w == 0.0


def test_export_adds_house_deficit_and_stops_at_min_soc():
    out = _run("export", -4000.0, solar_w=0.0, house_w=1200.0, sell=4.5, buy=6.0)
    assert out.battery_discharge_power_w == 5200.0
    empty = _run("export", -4000.0, solar_w=0.0, house_w=1200.0, sell=4.5, buy=6.0, soc=8.0)
    assert empty.battery_discharge_power_w == 0.0


def test_solar_charge_keeps_the_grid_part_rule():
    out = _run("solar_charge", 876.0, solar_w=1300.0, house_w=1600.0, buy=1.53, sell=0.54,
               hold=True, grid_charge_w=263.0)
    assert out.battery_charge_power_w == 263.0
    assert out.battery_discharge_power_w == 0.0
