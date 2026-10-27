"""DP-planeraren och fasutrymmet (fas B4.1).

26 okt 2026 planerade plan B 7,9 kW laddning medan Legionellans patron drog
~6 kW på L1/L3; exekutorns fasskydd ströp laddningen. DP ska se kända laster
och planera laddningen till kvartar där fasutrymmet finns.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from custom_components.smart_energy_manager.const import (
    CONF_LEGIONELLA_DURATION_MINUTES,
    CONF_LEGIONELLA_ENABLED,
    CONF_LEGIONELLA_MAX_INTERVAL_DAYS,
    CONF_LEGIONELLA_MIN_INTERVAL_DAYS,
)
from custom_components.smart_energy_manager.dp_planner import DPPlanner
from custom_components.smart_energy_manager.legionella import LegionellaManager
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule,
    PriceScheduler,
    PriceSlot,
)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 10, 26, 4, 0, tzinfo=TZ)
CHEAP = (NOW + timedelta(hours=2), NOW + timedelta(hours=3))  # 06–07, enda billiga timmen
PATRON = (NOW + timedelta(hours=2), NOW + timedelta(hours=2, minutes=45))


def _schedule():
    sch = PriceScheduler()
    slots = []
    for q in range(24 * 4):
        start = NOW + timedelta(minutes=15 * q)
        spot = -0.02 if CHEAP[0] <= start < CHEAP[1] else (1.50 if 17 <= start.hour < 21 else 0.30)
        slots.append(PriceSlot(start=start, end=start + timedelta(minutes=15), spot_sek=spot,
                               buy_sek=sch._buy_price(spot), sell_sek=sch._sell_price(spot)))
    return PriceSchedule(slots=slots)


def _plan(**kwargs):
    return DPPlanner(battery_min_soc=8, battery_max_soc=99).build_plan(
        now=NOW, battery_soc_pct=30, battery_capacity_kwh=30.69, battery_max_power_kw=8,
        ps=_schedule(), predicted_daily_kwh=28.8, house_load_w=1200, **kwargs,
    )


def _charge_w(plan, start, end):
    return [s.target_power_w for s in plan.slots if start <= s.start < end and s.action == "grid_charge"]


def test_without_phase_limit_dp_charges_full_power_through_the_patron():
    plan = _plan(known_loads=[(*PATRON, 6.0, ("L1", "L3"))])
    assert max(_charge_w(plan, *PATRON)) > 7000


def test_phase_limit_caps_charging_while_the_patron_runs():
    plan = _plan(known_loads=[(*PATRON, 6.0, ("L1", "L3"))], phase_limit_a=15.0)
    # 3 × (15 A × 230 V − 0,4 kW grundlast − 3 kW patron) ≈ 0,15 kW
    assert all(w <= 500 for w in _charge_w(plan, *PATRON))
    # Efter patronen finns hela utrymmet kvar i den billiga timmen.
    assert max(_charge_w(plan, PATRON[1], CHEAP[1])) > 7000


def test_phase_limit_alone_allows_full_power_at_normal_load():
    plan = _plan(phase_limit_a=15.0)
    assert max(_charge_w(plan, *CHEAP)) > 7000


def test_known_load_is_added_to_the_planned_deficit():
    base = _plan(phase_limit_a=15.0)
    with_patron = _plan(known_loads=[(*PATRON, 6.0, ("L1", "L3"))], phase_limit_a=15.0)
    assert with_patron.slots[-1].battery_soc_est_pct <= base.slots[-1].battery_soc_est_pct


def _manager(last_run_days_ago: float):
    m = LegionellaManager(MagicMock(), {
        CONF_LEGIONELLA_ENABLED: True,
        CONF_LEGIONELLA_MIN_INTERVAL_DAYS: 5,
        CONF_LEGIONELLA_MAX_INTERVAL_DAYS: 9,
        CONF_LEGIONELLA_DURATION_MINUTES: 60,
    })
    m._loaded = True
    m._last_run = NOW - timedelta(days=last_run_days_ago)
    return m


def test_legionella_exposes_the_window_it_waits_for():
    m = _manager(5.2)
    run, _ = m.should_run_now(NOW, 0.0, switch_is_on=False, water_temp=50.0, price_schedule=_schedule())
    assert not run
    assert m.planned_window == (CHEAP[0], CHEAP[0] + timedelta(minutes=60))


def test_legionella_window_follows_a_running_program():
    m = _manager(5.2)
    started = NOW + timedelta(minutes=5)
    m.should_run_now(started, 0.0, switch_is_on=True, water_temp=50.0, price_schedule=_schedule())
    assert m.planned_window == (started, started + timedelta(minutes=60))


def test_legionella_has_no_window_before_it_is_due():
    m = _manager(2.0)
    m.should_run_now(NOW, 0.0, switch_is_on=False, water_temp=50.0, price_schedule=_schedule())
    assert m.planned_window is None
