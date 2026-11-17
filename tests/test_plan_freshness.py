"""Planen exekutorn följer – släpps när den är för gammal (fas B5.4.1)."""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.dp_planner import plan_if_fresh
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


def test_fresh_plan_is_used():
    p = _plan(5)
    assert plan_if_fresh(p, NOW) is p


def test_no_plan_gives_none():
    assert plan_if_fresh(None, NOW) is None


def test_stale_plan_is_dropped():
    assert plan_if_fresh(_plan(31), NOW) is None


def test_plan_within_grace_is_still_used():
    p = _plan(25)
    assert plan_if_fresh(p, NOW) is p
