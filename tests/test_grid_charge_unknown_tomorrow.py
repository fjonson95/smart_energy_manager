"""Regel 3 när morgondagens priser saknas (P05-reserven).

30 okt 2026 kl 10:30: reserven 24,7 kWh gav V 4,37 kr, och planen nätladdade
1,7 kWh för 3,14 kr strax före solen. Reserven ska skydda batteriet mot
urladdning, inte driva köp.
"""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.energy_planner import EnergyPlanner
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule,
    PriceScheduler,
    PriceSlot,
)

TZ = timezone(timedelta(hours=1))


def _plan(now, spot_at, soc_pct, tomorrow_solar_kwh=5.0):
    sch = PriceScheduler()
    slots = []
    start = now
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0)
    while start < midnight:
        spot = spot_at(start.hour)
        slots.append(PriceSlot(start=start, end=start + timedelta(minutes=15), spot_sek=spot,
                               buy_sek=sch._buy_price(spot), sell_sek=sch._sell_price(spot)))
        start += timedelta(minutes=15)
    return EnergyPlanner(battery_min_soc=8, battery_max_soc=99).build_plan(
        now=now, battery_soc_pct=soc_pct, battery_capacity_kwh=30.69, battery_max_power_kw=8,
        ps=PriceSchedule(slots=slots), predicted_daily_kwh=27,
        solar_forecast_tomorrow_kwh=tomorrow_solar_kwh, solar_takeover_dt=None, house_load_w=1200,
    )


def _grid_charges(plan):
    return [s for s in plan.slots if s.action == "grid_charge"]


def test_reserve_for_unknown_tomorrow_does_not_buy_expensive_power():
    plan = _plan(datetime(2026, 10, 30, 10, 15, tzinfo=TZ),
                 lambda h: 2.45 if 15 <= h < 19 else 1.75, 81)
    assert _grid_charges(plan) == []


def test_reserve_still_holds_the_battery_when_tomorrow_is_unknown():
    plan = _plan(datetime(2026, 10, 30, 10, 15, tzinfo=TZ),
                 lambda h: 2.45 if 15 <= h < 19 else 1.75, 81)
    assert plan.slots[-1].battery_soc_est_pct > 60


def test_cheap_night_still_charges_before_an_expensive_day():
    plan = _plan(datetime(2026, 10, 30, 0, 0, tzinfo=TZ),
                 lambda h: 0.30 if h < 5 else 2.50, 20)
    assert _grid_charges(plan)
    assert all(s.start.hour < 5 for s in _grid_charges(plan))
