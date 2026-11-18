"""Batteriets andel av solöverskottet – samma regel i _auto_mode och exekutorn (B5.4.2).

Steg 4:s gamla regel (ladda inte när sälj ≥ 0,80 kr) gav pannans soldriftstak
(pvmaxcomp) och varmvattendumpen överskott som exekutorn samtidigt laddade
batteriet med – i 107 av 377 sommarkvartar skilde sig andelen.
"""
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.smart_energy_manager.energy_controller import (
    EnergyController, EnergyState, plan_surplus_charge_w,
)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=TZ)


@pytest.mark.parametrize("action,v_charge,sell,expected", [
    ("solar_charge", 0.5, 1.66, 3000.0),   # laddande kvart tar allt oavsett pris
    ("grid_charge", 0.5, 1.66, 3000.0),
    ("export", 5.0, 0.20, 0.0),            # exportkvart lagrar aldrig
    ("cover_load", 2.5, 1.66, 3000.0),     # sälj < V_charge: lagra
    ("cover_load", 1.0, 1.66, 0.0),        # sälj ≥ V_charge: sälj
    ("idle", 2.5, 1.66, 3000.0),
    (None, None, 1.66, 3000.0),            # ingen plan: lagra allt
])
def test_share_rule(action, v_charge, sell, expected):
    assert plan_surplus_charge_w(action, v_charge, sell, 3000.0, True, 8000.0) == expected


def test_share_is_zero_when_battery_cannot_charge_and_capped_by_power():
    assert plan_surplus_charge_w("solar_charge", 2.5, 1.0, 3000.0, False, 8000.0) == 0.0
    assert plan_surplus_charge_w("solar_charge", 2.5, 1.0, 9000.0, True, 8000.0) == 8000.0


def _state(**kw):
    return EnergyState(
        solar_power_w=4300.0, house_load_w=1300.0, battery_soc_pct=50.0,
        battery_capacity_kwh=30.69, battery_max_power_kw=8.0,
        spot_price_sek_kwh=1.6, buy_price_sek_kwh=2.4, sell_price_sek_kwh=1.66,
        boiler_pvmaxcomp_max_kw=3.5, now=NOW, **kw,
    )


def _ctrl():
    return EnergyController(max_current_per_phase=25, battery_min_soc=8, battery_max_soc=99)


def test_boiler_gets_nothing_when_plan_stores_the_surplus():
    d = _ctrl().compute(_state(plan_action="cover_load", plan_marginal_value_charge_sek_kwh=2.5))
    assert d.battery_charge_power_w == pytest.approx(3000.0)
    assert d.boiler_pvmaxcomp_kw == 0.0


def test_boiler_gets_the_surplus_when_plan_sells_it():
    d = _ctrl().compute(_state(plan_action="cover_load", plan_marginal_value_charge_sek_kwh=1.0))
    assert d.battery_charge_power_w == 0.0
    assert d.boiler_pvmaxcomp_kw > 0.0


def test_without_plan_battery_takes_surplus_and_says_so():
    d = _ctrl().compute(_state())
    assert d.battery_charge_power_w == pytest.approx(3000.0)
    assert "ingen plan" in d.reason
    assert "ekonomisk topp" not in d.reason
