from datetime import datetime, timedelta, timezone

import pytest

from custom_components.smart_energy_manager.savings_tracker import DailySavingsTracker, day_savings_sek

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 10, 24, 18, 0, tzinfo=TZ)


def _run(tracker, start, seconds, step, **kw):
    t = start
    end = start + timedelta(seconds=seconds)
    while t <= end:
        tracker.update(t, **kw)
        t += timedelta(seconds=step)
    return t


def test_discharge_covering_house_saves_buy_price():
    tr = DailySavingsTracker()
    # Huset drar 2 kW, batteriet täcker allt: nätet 0, utan batteri hade nätet importerat 2 kW.
    _run(tr, START, 3600, 30, grid_w=0.0, battery_w=-2000.0, buy_sek=2.0, sell_sek=0.5)
    today = tr.today
    assert today["import_kwh"] == pytest.approx(0.0)
    assert today["ref_import_kwh"] == pytest.approx(2.0)
    assert today["battery_discharge_kwh"] == pytest.approx(2.0)
    assert day_savings_sek(today) == pytest.approx(4.0)


def test_charging_from_grid_costs_buy_price():
    tr = DailySavingsTracker()
    # Laddar 3 kW från nätet utan annan last: referensen hade inte importerat något.
    _run(tr, START, 3600, 30, grid_w=3000.0, battery_w=3000.0, buy_sek=1.0, sell_sek=0.3)
    assert day_savings_sek(tr.today) == pytest.approx(-3.0)


def test_storing_solar_instead_of_exporting_costs_sell_price():
    tr = DailySavingsTracker()
    # 2 kW solöverskott laddas in i stället för att exporteras.
    _run(tr, START, 3600, 30, grid_w=0.0, battery_w=2000.0, buy_sek=1.5, sell_sek=0.4)
    assert tr.today["ref_export_kwh"] == pytest.approx(2.0)
    assert day_savings_sek(tr.today) == pytest.approx(-0.8)


def test_gap_longer_than_max_step_is_not_integrated():
    tr = DailySavingsTracker()
    tr.update(START, grid_w=0.0, battery_w=-2000.0, buy_sek=2.0, sell_sek=0.5)
    tr.update(START + timedelta(minutes=30), grid_w=0.0, battery_w=-2000.0, buy_sek=2.0, sell_sek=0.5)
    assert tr.today["ref_import_kwh"] == 0.0


def test_pause_restarts_integration():
    tr = DailySavingsTracker()
    tr.update(START, grid_w=0.0, battery_w=-2000.0, buy_sek=2.0, sell_sek=0.5)
    tr.pause()
    tr.update(START + timedelta(seconds=30), grid_w=0.0, battery_w=-2000.0, buy_sek=2.0, sell_sek=0.5)
    assert tr.today["ref_import_kwh"] == 0.0


def test_day_rollover_moves_today_to_history():
    tr = DailySavingsTracker()
    late = START.replace(hour=23, minute=58)
    _run(tr, late, 90, 30, grid_w=0.0, battery_w=-2000.0, buy_sek=2.0, sell_sek=0.5)
    assert tr.today["date"] == "2026-10-24"
    rolled = tr.update(START.replace(hour=23, minute=59, second=59) + timedelta(seconds=2),
                       grid_w=0.0, battery_w=-2000.0, buy_sek=2.0, sell_sek=0.5)
    assert rolled
    assert tr.today["date"] == "2026-10-25"
    assert tr.history[-1]["date"] == "2026-10-24"
    assert tr.history[-1]["savings_sek"] == pytest.approx(0.1, abs=0.01)


def test_store_roundtrip():
    tr = DailySavingsTracker()
    _run(tr, START, 600, 30, grid_w=500.0, battery_w=-1000.0, buy_sek=2.0, sell_sek=0.5)
    restored = DailySavingsTracker()
    restored.load(tr.to_dict())
    assert restored.today_summary() == tr.today_summary()
