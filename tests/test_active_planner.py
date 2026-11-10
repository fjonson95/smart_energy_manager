"""Val av planerare som styr exekutorn (fas B5.1)."""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.const import PLANNER_DP, PLANNER_V
from custom_components.smart_energy_manager.dp_planner import select_active_plan
from custom_components.smart_energy_manager.energy_planner import DayPlan

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=TZ)


def _plan(built_minutes_ago: int) -> DayPlan:
    generated = NOW - timedelta(minutes=built_minutes_ago)
    return DayPlan(
        generated_at=generated, valid_until=generated + timedelta(minutes=15),
        evening_target_soc_pct=10.0, export_floor_kwh=2.5, total_exportable_kwh=0.0,
        expected_revenue_sek=0.0, solar_takeover_dt=None, hourly_load_kw=1.2,
    )


def test_v_is_used_when_selected():
    v, b = _plan(5), _plan(5)
    assert select_active_plan(PLANNER_V, v, b, NOW) == (v, PLANNER_V)


def test_plan_b_is_used_when_selected_and_fresh():
    v, b = _plan(5), _plan(5)
    assert select_active_plan(PLANNER_DP, v, b, NOW) == (b, PLANNER_DP)


def test_falls_back_to_v_without_plan_b():
    v = _plan(5)
    assert select_active_plan(PLANNER_DP, v, None, NOW) == (v, PLANNER_V)


def test_falls_back_to_v_when_plan_b_is_stale():
    v, b = _plan(5), _plan(31)
    assert select_active_plan(PLANNER_DP, v, b, NOW) == (v, PLANNER_V)


def test_plan_b_within_grace_is_still_used():
    v, b = _plan(5), _plan(25)
    assert select_active_plan(PLANNER_DP, v, b, NOW) == (b, PLANNER_DP)
