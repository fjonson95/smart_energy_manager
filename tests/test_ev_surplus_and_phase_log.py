"""Billaddning på solöverskott och fasskyddets varningar (11 nov 2026).

16:15 exporterade plan B 8 kW ur batteriet med 0 W sol; exporten räknades som
solöverskott och Amina beordrades 13–16 A. Natten innan loggade fasskyddet en
varning var 30:e sekund medan det bara ströp nattladdningen.
"""
import logging
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.energy_controller import (
    CarConfig,
    ChargerConfig,
    ChargerState,
    ControlDecision,
    EnergyController,
    EnergyState,
)
from custom_components.smart_energy_manager.price_scheduler import PriceSchedule, PriceSlot

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 11, 16, 15, tzinfo=TZ)


def _charger() -> ChargerState:
    cfg = ChargerConfig(name="Amina", charger_switch="switch.amina", charger_current="number.amina",
                        cars=[CarConfig(name="Kia", car_phases=1)])
    return ChargerState(config=cfg, connected=True, active_car_name="Kia", soc_pct=50.0)


def _state(solar_w: float, battery_w: float, grid_per_phase_w: float, house_w: float = 1500.0) -> EnergyState:
    slot = PriceSlot(start=NOW, end=NOW + timedelta(minutes=15), spot_sek=4.25, buy_sek=6.25, sell_sek=4.32)
    # Billiga nattkvartar, annars är kvällens kvart "billig timme" för bilen.
    night = [PriceSlot(start=NOW + timedelta(hours=9, minutes=15 * i), end=NOW + timedelta(hours=9, minutes=15 * (i + 1)),
                       spot_sek=1.5, buy_sek=2.8, sell_sek=1.57) for i in range(16)]
    return EnergyState(
        solar_power_w=solar_w, house_load_w=house_w, battery_soc_pct=60.0, battery_power_w=battery_w,
        battery_capacity_kwh=30.69, battery_max_power_kw=8.0,
        grid_power_l1=grid_per_phase_w, grid_power_l2=grid_per_phase_w, grid_power_l3=grid_per_phase_w,
        chargers=[_charger()], spot_price_sek_kwh=4.25, buy_price_sek_kwh=6.25, sell_price_sek_kwh=4.32,
        price_schedule=PriceSchedule(slots=[slot] + night), now=NOW,
    )


def _ctrl() -> EnergyController:
    return EnergyController(max_current_per_phase=17, battery_min_soc=8, battery_max_soc=99)


def test_battery_export_is_not_solar_surplus_for_ev():
    # 8 kW ur batteriet, huset 1,5 kW → 6,5 kW export, ingen sol.
    decision = _ctrl().compute(_state(solar_w=0.0, battery_w=-8000.0, grid_per_phase_w=-6500.0 / 3))
    assert not any(d.enable for d in decision.charger_decisions)


def test_real_solar_export_still_charges_ev():
    # 6 kW sol, huset 1,5 kW, batteriet står → 4,5 kW export räcker till 1-fas.
    decision = _ctrl().compute(_state(solar_w=6000.0, battery_w=0.0, grid_per_phase_w=-4500.0 / 3))
    assert any(d.enable for d in decision.charger_decisions)


def _phase_state(grid_current_l2: float) -> EnergyState:
    return EnergyState(
        battery_soc_pct=50.0, battery_capacity_kwh=30.69, battery_max_power_kw=8.0,
        grid_power_l1=2000.0, grid_power_l2=grid_current_l2 * 230.0, grid_power_l3=2000.0,
        grid_current_l1=8.7, grid_current_l2=grid_current_l2, grid_current_l3=8.7, now=NOW,
    )


def test_throttling_a_charge_decision_is_not_a_warning(caplog):
    # Uppmätt L2 13 A, beslutet vill ladda 7,8 kW → strypt utan varning.
    ctrl = _ctrl()
    decision = ControlDecision(battery_charge_power_w=7800.0)
    with caplog.at_level(logging.WARNING):
        out = ctrl._apply_phase_limits(_phase_state(13.0), decision)
    assert out.battery_charge_power_w < 7800.0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_measured_overload_is_still_a_warning(caplog):
    ctrl = _ctrl()
    with caplog.at_level(logging.WARNING):
        ctrl._apply_phase_limits(_phase_state(18.0), ControlDecision())
    assert any("L2" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
