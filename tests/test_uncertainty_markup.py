"""Riskpåslaget på V från produktionskvoten (P3-2).

28–29 okt 2026 föll kvoten till 0,74–0,84 (höstens prognosbrus, inga
snötäckta paneler). Rampen började vid 0,9 och gav +40–87 % på V, så ett
fullt batteri hölls medan huset köpte för 3,4–3,8 kr.
"""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.smart_energy_manager.energy_planner import (
    EnergyPlanner,
    _uncertainty_markup,
)
from custom_components.smart_energy_manager.price_scheduler import (
    PriceSchedule,
    PriceScheduler,
    PriceSlot,
)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 10, 29, 18, 0, tzinfo=TZ)


@pytest.mark.parametrize("ratio, markup", [
    (1.24, 0.10), (0.84, 0.10), (0.74, 0.10), (0.6, 0.10), (0.45, 1.55), (0.3, 3.0), (0.0, 3.0),
])
def test_markup_ramps_only_below_0_6(ratio, markup):
    assert _uncertainty_markup(ratio) == pytest.approx(markup)


def _build_plan(pv_production_ratio: float):
    sch = PriceScheduler()
    slots = []
    for q in range(30 * 4):
        start = NOW + timedelta(minutes=15 * q)
        spot = 2.00 if start.day == 29 and start.hour < 21 else 1.60
        slots.append(PriceSlot(
            start=start,
            end=start + timedelta(minutes=15),
            spot_sek=spot,
            buy_sek=sch._buy_price(spot),
            sell_sek=sch._sell_price(spot),
        ))
    return EnergyPlanner(battery_min_soc=8, battery_max_soc=99).build_plan(
        now=NOW,
        battery_soc_pct=96,
        battery_capacity_kwh=30.69,
        battery_max_power_kw=8,
        ps=PriceSchedule(slots=slots),
        predicted_daily_kwh=27,
        solar_forecast_tomorrow_kwh=8.0,
        solar_takeover_dt=None,
        house_load_w=1200,
        pv_production_ratio=pv_production_ratio,
    )


def test_full_battery_covers_the_evening_peak_at_autumn_ratio():
    plan = _build_plan(0.84)
    assert plan.slots[0].target_power_w < 0, plan.slots[0].reason
