"""Varmvattendumpen och fasskyddet för Extra VV (beslut 2026-10-24, alternativ 1).

Pannan värmer med kompressorn (~3,5 kW på heat_pump_phase) när tanken är under
~47 °C och annars med patronen (~6 kW på patronfaserna). Siffrorna kommer från
mätningarna 23 och 24 okt 2026.
"""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.energy_controller import (
    ControlDecision,
    EnergyController,
    EnergyState,
)
from custom_components.smart_energy_manager.energy_planner import DayPlan, PlannedSlot
from custom_components.smart_energy_manager.price_scheduler import PriceSchedule, PriceSlot

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 24, 14, 25, tzinfo=TZ)


def _state(**kw) -> EnergyState:
    sell = kw.pop("sell", 0.20)
    buy = kw.pop("buy", 2.00)
    # Elmätaren ser solöverskottet som export, jämnt fördelat på faserna.
    if "grid_power_l1" not in kw:
        net_per_phase = (kw.get("house_load_w", 1000.0) - kw.get("solar_power_w", 0.0)) / 3.0
        kw.update(grid_power_l1=net_per_phase, grid_power_l2=net_per_phase, grid_power_l3=net_per_phase)
    slot = PriceSlot(start=NOW, end=NOW + timedelta(minutes=15), spot_sek=sell - 0.065, buy_sek=buy, sell_sek=sell)
    base = dict(
        solar_power_w=0.0,
        house_load_w=1000.0,
        battery_soc_pct=99.0,  # fullt: dumpen triggas av "batteri fullt"
        battery_capacity_kwh=30.69,
        battery_max_power_kw=8.0,
        hot_water_temp_c=50.0,
        heat_pump_phase="L2",
        heat_pump_patron_phases=["L1", "L3"],
        heat_pump_patron_power_kw=6.0,
        spot_price_sek_kwh=sell - 0.065,
        buy_price_sek_kwh=buy,
        sell_price_sek_kwh=sell,
        price_schedule=PriceSchedule(slots=[slot]),
        now=NOW,
    )
    base.update(kw)
    return EnergyState(**base)


def _ctrl() -> EnergyController:
    return EnergyController(max_current_per_phase=17, battery_min_soc=8, battery_max_soc=99)


def test_patron_dump_needs_surplus_for_full_patron():
    # Tank 50 °C → patron 6 kW. 5 kW överskott räcker inte längre (förr: 70 % = 4,2 kW).
    assert not _ctrl().compute(_state(solar_power_w=6000.0)).extra_hot_water
    assert _ctrl().compute(_state(solar_power_w=7200.0)).extra_hot_water


def test_compressor_dump_when_tank_is_cool():
    # Tank 45 °C → kompressor 3,5 kW: 3,6 kW överskott räcker.
    decision = _ctrl().compute(_state(solar_power_w=4600.0, hot_water_temp_c=45.0))
    assert decision.extra_hot_water
    assert "kompressor" in decision.reason


def test_patron_dump_not_profitable_when_sell_price_beats_heat_value():
    # Patronens värme är värd köp / 3,5 = 0,57 kr; säljpris 0,80 kr → sälj hellre.
    assert not _ctrl().compute(_state(solar_power_w=9000.0, sell=0.80)).extra_hot_water
    # Kompressorn ger 3,5 ggr mer värme per kWh – då lönar det sig.
    assert _ctrl().compute(_state(solar_power_w=9000.0, sell=0.80, hot_water_temp_c=45.0)).extra_hot_water


def test_running_dump_counts_its_own_load_back():
    # Patronen går och drar 5,8 kW, som syns i huslasten. Solen 7,5 kW, huset
    # utan patron 1 kW → överskottet exklusive dumpen är ~0,7 kW. Dumpen ska
    # ändå fortsätta (förr: "inget överskott" → av/på var 11:e minut).
    state = _state(
        solar_power_w=7500.0, house_load_w=6800.0, extra_hot_water_on=True,
        heat_pump_phase_current_a={"L1": 12.6, "L2": 0.2, "L3": 12.6},
    )
    decision = _ctrl().compute(state)
    assert decision.extra_hot_water
    assert not decision.extra_hot_water_hard_stop


def test_running_dump_hard_stops_when_sun_is_gone():
    state = _state(
        solar_power_w=1500.0, house_load_w=6800.0, extra_hot_water_on=True,
        extra_hot_water_locked_on=True,
        heat_pump_phase_current_a={"L1": 12.6, "L2": 0.2, "L3": 12.6},
    )
    decision = _ctrl().compute(state)
    assert not decision.extra_hot_water
    assert decision.extra_hot_water_hard_stop


def test_locked_dump_stays_on_while_surplus_partly_covers():
    # Inom minimitiden och överskottet täcker > 50 % → låst PÅ, inget hårt stopp.
    state = _state(
        solar_power_w=5000.0, house_load_w=6800.0, extra_hot_water_on=True,
        extra_hot_water_locked_on=True,
        heat_pump_phase_current_a={"L1": 12.6, "L2": 0.2, "L3": 12.6},
    )
    decision = _ctrl().compute(state)
    assert decision.extra_hot_water
    assert not decision.extra_hot_water_hard_stop


def _executor_case(heat_pump_currents, locked):
    """24 okt 14:32: kompressorn värmer varmvatten på L2, solen är borta och
    planen vill täcka huset (~3,9 kW underskott) från batteriet."""
    state = _state(
        solar_power_w=1300.0, house_load_w=5200.0, battery_soc_pct=60.0,
        extra_hot_water_on=True, extra_hot_water_locked_on=locked,
        hot_water_temp_c=45.3,
        heat_pump_phase_current_a=heat_pump_currents,
        grid_power_l1=-300.0, grid_power_l2=3900.0, grid_power_l3=200.0,
        grid_current_l1=1.3, grid_current_l2=17.4, grid_current_l3=1.0,
        plan_action="cover_load",
    )
    slot = PlannedSlot(start=NOW, end=NOW + timedelta(minutes=15), action="cover_load",
                       target_power_w=-3900.0, battery_soc_est_pct=60.0, reason="test")
    plan = DayPlan(
        generated_at=NOW, valid_until=NOW + timedelta(hours=1), evening_target_soc_pct=10.0,
        export_floor_kwh=3.0, total_exportable_kwh=0.0, expected_revenue_sek=0.0,
        solar_takeover_dt=None, hourly_load_kw=1.5, slots=[slot],
    )
    ctrl = _ctrl()
    decision = ctrl.compute(state)
    return ctrl.apply_plan_executor(plan, "auto", state, decision, NOW, 0.0)


def test_no_false_export_when_compressor_runs_on_l2():
    # Förr drog fasskyddet bort patronen (3 kW) från L1 och L3 → "export 20 A"
    # → urladdningen ströps till 39–600 W. Nu ska batteriet få täcka huset.
    decision = _executor_case({"L1": 0.2, "L2": 15.0, "L3": 0.3}, locked=True)
    assert decision.battery_discharge_power_w > 3000.0
