"""Planeraren håller ett laddat batteri genom dyra timmar (HANDOFF.md 4.1).

Syntetiskt vinterdygn: billig natt (spot 0,40), morgontopp 06–09 (2,00),
kvällstopp 17–20 (1,60), ingen sol, 2 kW last. Testet beskriver önskat
beteende och är markerat xfail tills planeraren ersätts (fas B).
"""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.smart_energy_manager.energy_planner import EnergyPlanner
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule,
    PriceScheduler,
    PriceSlot,
)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 1, 20, 22, 0, tzinfo=TZ)


def _spot_at(hour: int) -> float:
    if hour < 6:
        return 0.40
    if hour < 9:
        return 2.00
    if 17 <= hour < 20:
        return 1.60
    return 1.00


def _build_plan(soc_pct: float):
    sch = PriceScheduler()
    slots = []
    for q in range(26 * 4):
        start = NOW + timedelta(minutes=15 * q)
        spot = _spot_at(start.hour)
        slots.append(PriceSlot(
            start=start,
            end=start + timedelta(minutes=15),
            spot_sek=spot,
            buy_sek=sch._buy_price(spot),
            sell_sek=sch._sell_price(spot),
        ))
    return EnergyPlanner(battery_min_soc=10, battery_max_soc=95).build_plan(
        now=NOW,
        battery_soc_pct=soc_pct,
        battery_capacity_kwh=33,
        battery_max_power_kw=8,
        ps=PriceSchedule(slots=slots),
        predicted_daily_kwh=50,
        solar_forecast_tomorrow_kwh=2.0,
        solar_takeover_dt=None,
        house_load_w=2000,
    )


def _discharges_in(plan, hour_from: int, hour_to: int) -> bool:
    return any(
        s.target_power_w < 0
        for s in plan.slots
        if s.start.date() > NOW.date() and hour_from <= s.start.hour < hour_to
    )


@pytest.mark.xfail(strict=True, reason="HANDOFF.md 4.1: V-heuristiken håller batteriet genom topparna (fas B)")
@pytest.mark.parametrize("soc_pct", [15, 50])
def test_battery_discharges_through_both_peaks(soc_pct):
    plan = _build_plan(soc_pct)
    assert _discharges_in(plan, 6, 9)
    assert _discharges_in(plan, 17, 20)
