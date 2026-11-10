"""Antagen morgondag innan morgondagens priser publiceras.

9 nov 2026 kl 09:15: låg solprognos för morgondagen gjorde P05-golvet lika
stort som hela batteriet. V föll tillbaka på dygnets högsta möjlighet (7,61 kr)
och huset köpte för 4–5 kr med batteriet på 99 %. Med dagens priser som antagen
morgondag väger planen dagens topp mot morgondagens behov i stället.
"""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.energy_planner import EnergyPlanner
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule,
    PriceScheduler,
    PriceSlot,
)

TZ = timezone(timedelta(hours=1))


def _plan(now, spot_at, soc_pct, with_reference=True):
    sch = PriceScheduler()
    day = []
    start = now.replace(hour=0, minute=0)
    while start < now.replace(hour=0, minute=0) + timedelta(days=1):
        spot = spot_at(start.hour)
        day.append(PriceSlot(start=start, end=start + timedelta(minutes=15), spot_sek=spot,
                             buy_sek=sch._buy_price(spot), sell_sek=sch._sell_price(spot)))
        start += timedelta(minutes=15)
    ps = PriceSchedule(slots=[s for s in day if s.end > now])
    if with_reference:
        ps.reference_day = day
    return EnergyPlanner(battery_min_soc=8, battery_max_soc=99).build_plan(
        now=now, battery_soc_pct=soc_pct, battery_capacity_kwh=30.69, battery_max_power_kw=8,
        ps=ps, predicted_daily_kwh=35, solar_forecast_tomorrow_kwh=4.6,
        solar_takeover_dt=None, house_load_w=1600,
    )


def _nov9(hour):
    if 15 <= hour < 20:
        return 4.6
    if hour >= 21 or hour < 6:
        return 2.0
    return 2.9


def test_full_battery_covers_todays_peak_when_tomorrow_is_unknown():
    plan = _plan(datetime(2026, 11, 9, 9, 15, tzinfo=TZ), _nov9, 99)
    assert any(s.target_power_w < 0 for s in plan.slots if 15 <= s.start.hour < 20)
    assert "antagna" in plan.notes


def test_old_floor_locked_the_battery_in_the_same_situation():
    plan = _plan(datetime(2026, 11, 9, 9, 15, tzinfo=TZ), _nov9, 99, with_reference=False)
    assert not any(s.target_power_w < 0 for s in plan.slots)


def test_flat_day_keeps_energy_for_tomorrow():
    plan = _plan(datetime(2026, 10, 30, 10, 15, tzinfo=TZ),
                 lambda h: 2.45 if 15 <= h < 19 else 1.75, 81)
    assert plan.slots[-1].battery_soc_est_pct > 50
    assert not [s for s in plan.slots if s.action == "grid_charge"]


def test_assumed_tomorrow_never_appears_in_the_plan():
    now = datetime(2026, 11, 9, 9, 15, tzinfo=TZ)
    plan = _plan(now, _nov9, 99)
    assert max(s.start for s in plan.slots) < datetime(2026, 11, 10, tzinfo=TZ)


_NOV10_BUY = [2.42, 2.43, 2.43, 2.42, 2.45, 2.6, 3.9, 5.0, 5.8, 5.7, 5.74, 4.77,
              5.25, 4.96, 5.15, 6.22, 7.14, 7.06, 6.82, 6.23, 5.81, 4.79, 4.2, 3.64]


def test_expensive_day_uses_the_battery_today_and_refills_at_night():
    plan = _plan(datetime(2026, 11, 10, 9, 30, tzinfo=TZ),
                 lambda h: _NOV10_BUY[h] / 1.25 - 0.749, 89)
    assert any(s.target_power_w < 0 for s in plan.slots if 10 <= s.start.hour < 15)
    assert min(s.battery_soc_est_pct for s in plan.slots) < 60
