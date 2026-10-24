"""Extra varmvatten efter negativt pris tidigare i dag kräver solöverskott.

negative_slots_passed_today är > 0 resten av dygnet efter en negativ kvart.
Utan överskottskrav skulle patronen (~6 kW) starta på nätel i kvällstoppen.
"""
from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.energy_controller import EnergyController, EnergyState
from custom_components.smart_energy_manager.price_scheduler import PriceSchedule, PriceSlot

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 6, 14, 18, 0, tzinfo=TZ)


def _state(solar_w: float) -> EnergyState:
    slot = PriceSlot(start=NOW, end=NOW + timedelta(minutes=15), spot_sek=1.50, buy_sek=3.10, sell_sek=1.57)
    return EnergyState(
        solar_power_w=solar_w,
        house_load_w=1000.0,
        battery_soc_pct=60.0,
        battery_capacity_kwh=30.69,
        battery_max_power_kw=5.0,
        hot_water_temp_c=50.0,
        spot_price_sek_kwh=1.50,
        buy_price_sek_kwh=3.10,
        sell_price_sek_kwh=1.57,
        price_schedule=PriceSchedule(slots=[slot], negative_slots_passed_today=8),
    )


def test_no_extra_hot_water_after_negative_price_without_surplus():
    decision = EnergyController().compute(_state(solar_w=0.0))
    assert not decision.extra_hot_water


def test_extra_hot_water_after_negative_price_with_surplus():
    decision = EnergyController().compute(_state(solar_w=20000.0))
    assert decision.extra_hot_water
    assert "passerat neg pris" in decision.reason
