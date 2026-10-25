"""Dygnsnivån för DP-planeraren (fas B3/B4): 7 dygns snitt + k × graddagsdelta."""
from datetime import date, timedelta

import pytest

from custom_components.smart_energy_manager.const import DP_LOAD_BALANCE_TEMP_C, DP_LOAD_K_KWH_DD
from custom_components.smart_energy_manager.dp_planner import calibrated_daily_levels

TODAY = date(2026, 1, 20)


def _history(net_kwh, temps):
    return [
        {"date": str(TODAY - timedelta(days=len(net_kwh) - i)), "net_kwh": n, "mean_temp_c": t}
        for i, (n, t) in enumerate(zip(net_kwh, temps))
    ]


def test_empty_history_gives_none():
    assert calibrated_daily_levels([], TODAY) is None


def test_without_temperatures_the_level_is_the_seven_day_mean():
    levels = calibrated_daily_levels(_history([40, 42, 44, 46, 48, 50, 52], [None] * 7), TODAY)
    assert levels == {TODAY + timedelta(days=o): pytest.approx(46.0) for o in (0, 1, 2)}


def test_only_the_last_seven_days_count():
    levels = calibrated_daily_levels(_history([100] + [40] * 7, [0.0] * 8), TODAY)
    assert levels[TODAY] == pytest.approx(40.0)


def test_persistence_uses_the_latest_known_day_temperature():
    # Snitt −2 °C, gårdagen 0 °C ⇒ 2 graddagar mindre än snittet.
    temps = [-3.0, -3.0, -3.0, -1.0, -1.0, -3.0, 0.0]
    levels = calibrated_daily_levels(_history([45] * 7, temps), TODAY)
    dd_mean = sum(DP_LOAD_BALANCE_TEMP_C - t for t in temps) / 7
    expected = 45 + DP_LOAD_K_KWH_DD * ((DP_LOAD_BALANCE_TEMP_C - 0.0) - dd_mean)
    assert levels[TODAY] == pytest.approx(expected)
    assert levels[TODAY + timedelta(days=2)] == pytest.approx(expected)


def test_forecast_temperature_is_used_per_day():
    levels = calibrated_daily_levels(
        _history([45] * 7, [0.0] * 7), TODAY, {TODAY + timedelta(days=1): -10.0},
    )
    assert levels[TODAY] == pytest.approx(45.0)
    assert levels[TODAY + timedelta(days=1)] == pytest.approx(45 + DP_LOAD_K_KWH_DD * 10)


def test_warm_days_do_not_count_degree_days_below_zero():
    levels = calibrated_daily_levels(
        _history([20] * 7, [20.0] * 7), TODAY, {TODAY + timedelta(days=1): 25.0},
    )
    assert levels[TODAY + timedelta(days=1)] == pytest.approx(20.0)


def test_old_entries_without_temperature_still_count_in_the_mean():
    history = _history([30, 30, 30, 60, 60, 60, 60], [None, None, None, 0.0, 0.0, 0.0, 0.0])
    levels = calibrated_daily_levels(history, TODAY)
    assert levels[TODAY] == pytest.approx(sum([30] * 3 + [60] * 4) / 7)
