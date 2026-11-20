"""Snötäckta solpaneler: planens solprognos räknas ned (19 nov 2026).

Panelerna gav 0,7 kWh mot prognosen 6,0 kWh. Planen för nästa dag nätladdade
bara ~4 kWh på natten och räknade med ~9 kWh sol till kvällstoppen.
"""
from datetime import date, datetime, timedelta, timezone

from custom_components.smart_energy_manager.dp_planner import DPPlanner
from custom_components.smart_energy_manager.price_scheduler import PriceSchedule, PriceSlot
from custom_components.smart_energy_manager.solar_snow import (
    NEW_SNOW_RATIO,
    SnowState,
    solar_factors,
    update_snow_state,
)

TODAY = date(2026, 11, 19)
TOMORROW = TODAY + timedelta(days=1)


def test_low_production_in_frost_is_snow():
    s = update_snow_state(SnowState(), TODAY, 0.7, 6.0, 0.0, 0.5, -4.2)
    assert s.covered and s.ratio == 0.12 and s.since == "2026-11-19"


def test_low_production_above_zero_is_just_clouds():
    assert not update_snow_state(SnowState(), TODAY, 0.7, 6.0, 0.0, 0.5, 2.0).covered


def test_too_little_forecast_to_judge_keeps_state():
    covered = SnowState(True, 0.12, "2026-11-19")
    assert update_snow_state(covered, TOMORROW, 0.0, 0.3, None, 0.1, -6.0) == covered
    assert update_snow_state(SnowState(), TODAY, 0.0, 0.3, None, 0.1, -6.0) == SnowState()


def test_production_back_in_the_last_hour_clears():
    covered = SnowState(True, 0.12, "2026-11-19")
    assert not update_snow_state(covered, TOMORROW, 0.9, 3.0, 0.8, 1.2, -1.0).covered


def test_cleared_today_does_not_flap_back_on_the_day_ratio():
    # 20 nov 10:56: snön gled av, men morgonen höll dagens kvot under 30 % –
    # tillståndet bytte mellan täckt och fritt var 30:e sekund till 11:00.
    s = update_snow_state(SnowState(True, 0.12, "2026-11-19"), TOMORROW, 1.1, 3.9, 0.7, 1.2, -1.0)
    assert not s.covered and s.cleared_on == TOMORROW.isoformat()
    for actual, so_far, hour, f_hour in ((1.1, 4.0, 0.7, 1.2), (1.2, 4.2, 0.8, 1.3), (1.4, 4.5, 0.9, 1.3)):
        s = update_snow_state(s, TOMORROW, actual, so_far, hour, f_hour, -1.0)
        assert not s.covered


def test_cleared_today_covers_again_on_a_dead_last_hour():
    s = SnowState(cleared_on=TOMORROW.isoformat())
    s = update_snow_state(s, TOMORROW, 4.0, 6.0, 0.1, 1.5, -2.0)
    assert s.covered and s.ratio == 0.07


def test_cleared_yesterday_judges_today_on_the_day_ratio_again():
    s = update_snow_state(SnowState(cleared_on=TODAY.isoformat()), TOMORROW, 0.2, 3.0, 0.0, 0.8, -3.0)
    assert s.covered


def test_covered_carries_until_forecast_thaw():
    covered = SnowState(True, 0.12, "2026-11-19")
    days = {TOMORROW: ("partlycloudy", -1.0, -6.0), TOMORROW + timedelta(days=1): ("sunny", 2.0, -3.0)}
    assert solar_factors(covered, TODAY, days) == {TODAY: 0.12, TOMORROW: 0.12}


def test_new_snow_in_the_forecast_covers():
    days = {TOMORROW: ("snowy", 0.5, -2.0)}
    # Dag 2 saknar prognos och ärver täckningen.
    assert solar_factors(SnowState(), TODAY, days) == {
        TOMORROW: NEW_SNOW_RATIO, TOMORROW + timedelta(days=1): NEW_SNOW_RATIO,
    }


def test_no_snow_no_factors():
    assert solar_factors(SnowState(), TODAY, {TOMORROW: ("sunny", -3.0, -8.0)}) == {}


TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 19, 22, 0, tzinfo=TZ)


def _night_charge_kwh(factors):
    slots = []
    for q in range(26 * 4):
        start = NOW + timedelta(minutes=15 * q)
        h = start.hour
        # Natten 2,6 kr: med sol i morgon lönar det sig att vänta på solen,
        # utan sol att ladda fullt till kvällstoppen (4,2 kr).
        buy = 2.6 if 0 <= h < 6 else 4.2 if 15 <= h < 20 else 3.0
        slot = PriceSlot(start=start, end=start + timedelta(minutes=15), spot_sek=buy / 1.25,
                         buy_sek=buy, sell_sek=buy * 0.6)
        if 10 <= h < 14 and start.date() == TOMORROW:
            slot.solar_kw = 5.0
            slot.solar_kwh = 5.0 * 0.25
        slots.append(slot)
    plan = DPPlanner(battery_min_soc=8, battery_max_soc=99).build_plan(
        now=NOW, battery_soc_pct=9, battery_capacity_kwh=30.69, battery_max_power_kw=8,
        ps=PriceSchedule(slots=slots), predicted_daily_kwh=48.0, house_load_w=2000,
        solar_factor_by_date=factors,
    )
    night = [s for s in plan.slots if s.start.date() == TOMORROW and s.start.hour < 6]
    return sum(max(0.0, s.target_power_w) * 0.25 / 1000.0 for s in night), plan


def test_plan_grid_charges_more_at_night_when_panels_are_covered():
    sunny_kwh, _ = _night_charge_kwh(None)
    snowy_kwh, plan = _night_charge_kwh({TOMORROW: 0.12})
    assert snowy_kwh > sunny_kwh + 5.0
    assert "snö på panelerna" in plan.notes
