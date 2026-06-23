"""Core energy control logic for Smart Energy Manager."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from .const import (
    PHASES, DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE,
    MIN_EV_CURRENT, MAX_EV_CURRENT, MIN_SOLAR_FOR_EV,
    NEGATIVE_PRICE_THRESHOLD,
    EV_PHASE_L1, EV_PHASE_L2, EV_PHASE_L3,
    MODE_AUTO, MODE_WINTER, MODE_FORCE_CHARGE_EV,
    MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PhaseLoad:
    """Current load per phase in Watts."""
    L1: float = 0.0
    L2: float = 0.0
    L3: float = 0.0

    def max_phase(self) -> float:
        return max(self.L1, self.L2, self.L3)

    def as_dict(self) -> dict:
        return {"L1": self.L1, "L2": self.L2, "L3": self.L3}


@dataclass
class EnergyState:
    """Current state of the energy system."""
    # Solar
    solar_power_w: float = 0.0
    solar_power_l1: float = 0.0
    solar_power_l2: float = 0.0
    solar_power_l3: float = 0.0
    solar_forecast_today_kwh: float = 0.0
    solar_forecast_tomorrow_kwh: float = 0.0

    # Battery
    battery_soc_pct: float = 50.0
    battery_power_w: float = 0.0        # + = charging, - = discharging
    battery_capacity_kwh: float = 10.0
    battery_max_power_kw: float = 5.0

    # EV
    ev_charging: bool = False
    ev_current_a: float = 0.0
    ev_power_w: float = 0.0
    ev_soc_pct: Optional[float] = None
    ev_phase: str = EV_PHASE_L1

    # Heat pump
    heat_pump_power_w: float = 0.0
    heat_pump_on: bool = False
    extra_hot_water_on: bool = False

    # Grid (positive = import, negative = export)
    grid_power_l1: float = 0.0
    grid_power_l2: float = 0.0
    grid_power_l3: float = 0.0
    grid_current_l1: float = 0.0
    grid_current_l2: float = 0.0
    grid_current_l3: float = 0.0

    # Pricing
    spot_price_sek_kwh: float = 0.0     # raw nordpool spot
    buy_price_sek_kwh: float = 0.0      # (spot + fees + tax) * vat
    sell_price_sek_kwh: float = 0.0     # spot + extra revenue

    # Mode
    operating_mode: str = MODE_AUTO
    force_ev_charge: bool = False
    winter_mode: bool = False


@dataclass
class ControlDecision:
    """Actions to take this control cycle."""
    battery_charge_power_w: float = 0.0    # 0 = idle, >0 = charge at this power
    battery_discharge_power_w: float = 0.0  # 0 = idle, >0 = discharge at this power
    ev_enable: bool = False
    ev_current_a: float = 0.0
    extra_hot_water: bool = False
    reason: str = ""
    phase_loads: PhaseLoad = field(default_factory=PhaseLoad)


class EnergyController:
    """
    Main energy control logic.

    Phase allocation (3-fas 20A max per fas):
    - Solar inverter: 3-fas, equal on all phases
    - Battery inverter: 3-fas, equal on all phases
    - EV charger: 3-fas HW, but one car charges on 1 phase only (configurable)
    - Heat pump: 1-fas
    - House loads: distributed across phases

    Priority order (self-consumption):
    1. Cover house loads
    2. Charge EV from solar if enough solar available
    3. Charge battery from solar surplus
    4. If battery full and solar surplus → extra hot water
    5. Discharge battery to cover house loads when no solar
    6. Winter mode: charge cheap hours, discharge expensive hours
    """

    def __init__(
        self,
        max_current_per_phase: float = DEFAULT_MAX_CURRENT,
        grid_voltage: float = DEFAULT_GRID_VOLTAGE,
        battery_min_soc: float = 10.0,
        battery_max_soc: float = 95.0,
        ev_soc_target: float = 80.0,
        winter_cheap_threshold: float = 0.80,
        winter_expensive_threshold: float = 1.50,
        winter_min_soc: float = 20.0,
        winter_max_soc: float = 95.0,
    ):
        self.max_current = max_current_per_phase
        self.voltage = grid_voltage
        self.max_phase_power = max_current_per_phase * grid_voltage  # W per phase
        self.battery_min_soc = battery_min_soc
        self.battery_max_soc = battery_max_soc
        self.ev_soc_target = ev_soc_target
        self.winter_cheap_threshold = winter_cheap_threshold
        self.winter_expensive_threshold = winter_expensive_threshold
        self.winter_min_soc = winter_min_soc
        self.winter_max_soc = winter_max_soc

    def compute(self, state: EnergyState) -> ControlDecision:
        """Compute control decisions based on current state."""
        decision = ControlDecision()

        if state.operating_mode == MODE_MANUAL:
            decision.reason = "Manual mode – no automatic control"
            return decision

        if state.operating_mode == MODE_FORCE_CHARGE_EV:
            return self._force_charge_ev(state)

        if state.operating_mode == MODE_FORCE_CHARGE_BATTERY:
            return self._force_charge_battery(state)

        if state.operating_mode == MODE_WINTER or state.winter_mode:
            return self._winter_mode(state)

        return self._auto_mode(state)

    # ------------------------------------------------------------------
    # AUTO MODE
    # ------------------------------------------------------------------
    def _auto_mode(self, state: EnergyState) -> ControlDecision:
        """Self-consumption optimized control."""
        decision = ControlDecision(reason="Auto mode")

        solar_w = state.solar_power_w
        battery_soc = state.battery_soc_pct
        buy_price = state.buy_price_sek_kwh
        sell_price = state.sell_price_sek_kwh
        negative_price = sell_price < NEGATIVE_PRICE_THRESHOLD

        # --- Estimate house load (excluding EV/battery) ---
        # We use grid + solar - battery_discharge as house load proxy
        house_load_w = max(0.0, (
            state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3
            + solar_w
            + (max(0, -state.battery_power_w))  # battery discharging adds power
            - max(0, state.battery_power_w)       # charging consumes power
        ))

        # --- Available solar surplus after house load ---
        solar_surplus_w = max(0.0, solar_w - house_load_w)

        _LOGGER.debug(
            "Auto: solar=%.0fW house=%.0fW surplus=%.0fW soc=%.0f%% buy=%.3f sell=%.3f neg=%s",
            solar_w, house_load_w, solar_surplus_w, battery_soc, buy_price, sell_price, negative_price
        )

        # --- Negative price handling ---
        # When sell price is negative and we have solar: absorb as much as possible
        if negative_price and solar_w > 0:
            decision.reason += " | Negative spot price – absorbing excess solar"
            # 1. Charge battery as much as possible
            if battery_soc < self.battery_max_soc:
                charge_power = min(
                    state.battery_max_power_kw * 1000,
                    solar_surplus_w
                )
                decision.battery_charge_power_w = charge_power
                solar_surplus_w -= charge_power

            # 2. Extra hot water
            if solar_surplus_w > 500:
                decision.extra_hot_water = True

            # 3. Charge EV if available
            if solar_surplus_w > MIN_SOLAR_FOR_EV:
                ev_current = self._solar_to_ev_current(solar_surplus_w, state.ev_phase)
                decision.ev_enable = True
                decision.ev_current_a = ev_current

            return self._apply_phase_limits(state, decision)

        # --- Normal auto: priority self-consumption ---

        # Step 1: Charge EV from solar if we have enough solar
        ev_enabled = False
        if solar_surplus_w >= MIN_SOLAR_FOR_EV:
            ev_current = self._solar_to_ev_current(solar_surplus_w, state.ev_phase)
            if ev_current >= MIN_EV_CURRENT:
                ev_surplus_after = solar_surplus_w - (ev_current * self.voltage)
                decision.ev_enable = True
                decision.ev_current_a = ev_current
                solar_surplus_w = max(0.0, ev_surplus_after)
                ev_enabled = True
                decision.reason += f" | EV solar charge {ev_current:.0f}A"

        # Step 2: Charge battery from remaining solar surplus
        if solar_surplus_w > 100 and battery_soc < self.battery_max_soc:
            charge_power = min(state.battery_max_power_kw * 1000, solar_surplus_w)
            decision.battery_charge_power_w = charge_power
            solar_surplus_w -= charge_power
            decision.reason += f" | Battery charging {charge_power:.0f}W from solar"

        # Step 3: Extra hot water if battery full and still surplus
        if solar_surplus_w > 500 and battery_soc >= self.battery_max_soc:
            decision.extra_hot_water = True
            decision.reason += " | Extra hot water (battery full)"

        # Step 4: Discharge battery to cover house load when no/little solar
        if solar_w < house_load_w and battery_soc > self.battery_min_soc:
            deficit_w = house_load_w - solar_w
            discharge_power = min(state.battery_max_power_kw * 1000, deficit_w)
            # Only discharge if buying electricity – not worth it at very low prices
            if buy_price > 0.20:  # Only discharge if price above 20 öre
                decision.battery_discharge_power_w = discharge_power
                decision.reason += f" | Battery discharge {discharge_power:.0f}W for house load"

        # Step 5: If battery below min and no solar → idle (preserve battery)
        if battery_soc <= self.battery_min_soc:
            decision.battery_discharge_power_w = 0
            decision.reason += " | Battery at min SOC"

        return self._apply_phase_limits(state, decision)

    # ------------------------------------------------------------------
    # WINTER MODE
    # ------------------------------------------------------------------
    def _winter_mode(self, state: EnergyState) -> ControlDecision:
        """
        Winter mode: charge battery during cheap hours (night),
        discharge during expensive hours (evening peak).
        """
        decision = ControlDecision(reason="Winter mode")
        hour = datetime.now().hour
        buy_price = state.buy_price_sek_kwh
        sell_price = state.sell_price_sek_kwh
        soc = state.battery_soc_pct
        solar_w = state.solar_power_w

        is_cheap = buy_price <= self.winter_cheap_threshold
        is_expensive = buy_price >= self.winter_expensive_threshold
        is_night = hour < 6 or hour >= 23

        _LOGGER.debug(
            "Winter: hour=%d price=%.3f cheap=%s expensive=%s soc=%.0f%%",
            hour, buy_price, is_cheap, is_expensive, soc
        )

        if is_cheap and (is_night or buy_price < 0.30):
            # Charge from grid if cheap
            if soc < self.winter_max_soc:
                decision.battery_charge_power_w = state.battery_max_power_kw * 1000
                decision.reason += f" | Charging battery (cheap price {buy_price:.3f} SEK)"
        elif is_expensive and soc > self.winter_min_soc:
            # Discharge during peak pricing
            decision.battery_discharge_power_w = state.battery_max_power_kw * 1000
            decision.reason += f" | Discharging battery (expensive {buy_price:.3f} SEK)"
        elif solar_w > 100 and soc < self.winter_max_soc:
            # Charge from solar when available
            house_load_w = max(0.0,
                state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3 + solar_w
            )
            surplus = max(0.0, solar_w - house_load_w)
            if surplus > 100:
                decision.battery_charge_power_w = min(
                    state.battery_max_power_kw * 1000, surplus
                )
                decision.reason += " | Charging battery from solar (winter)"

        # EV: only charge from solar in winter mode
        if solar_w > MIN_SOLAR_FOR_EV:
            ev_current = self._solar_to_ev_current(solar_w, state.ev_phase)
            if ev_current >= MIN_EV_CURRENT:
                decision.ev_enable = True
                decision.ev_current_a = ev_current

        return self._apply_phase_limits(state, decision)

    # ------------------------------------------------------------------
    # FORCE MODES
    # ------------------------------------------------------------------
    def _force_charge_ev(self, state: EnergyState) -> ControlDecision:
        """Force charge EV from grid at max allowed current."""
        decision = ControlDecision(reason="Force charge EV from grid")
        decision.ev_enable = True
        decision.ev_current_a = MAX_EV_CURRENT
        return self._apply_phase_limits(state, decision)

    def _force_charge_battery(self, state: EnergyState) -> ControlDecision:
        """Force charge battery from grid."""
        decision = ControlDecision(reason="Force charge battery from grid")
        if state.battery_soc_pct < self.battery_max_soc:
            decision.battery_charge_power_w = state.battery_max_power_kw * 1000
        return self._apply_phase_limits(state, decision)

    # ------------------------------------------------------------------
    # PHASE LIMIT ENFORCEMENT
    # ------------------------------------------------------------------
    def _apply_phase_limits(self, state: EnergyState, decision: ControlDecision) -> ControlDecision:
        """
        Enforce max 20A per phase.
        Reduce EV current and battery power if phase limits are exceeded.
        """
        # Build phase load map (positive = import from grid)
        # Solar and battery cancel out on all three phases equally
        solar_per_phase = state.solar_power_w / 3.0
        battery_discharge_per_phase = decision.battery_discharge_power_w / 3.0
        battery_charge_per_phase = decision.battery_charge_power_w / 3.0

        ev_load = {p: 0.0 for p in PHASES}
        if decision.ev_enable and decision.ev_current_a > 0:
            ev_power = decision.ev_current_a * self.voltage
            ev_load[state.ev_phase] = ev_power  # 1-phase car

        heat_pump_phase = "L2"  # assume heat pump on L2
        hp_load = {p: 0.0 for p in PHASES}
        hp_load[heat_pump_phase] = state.heat_pump_power_w

        loads: dict[str, float] = {}
        for phase in PHASES:
            grid_power = getattr(state, f"grid_power_{phase.lower()}", 0.0)
            loads[phase] = (
                grid_power
                - solar_per_phase
                + battery_charge_per_phase
                - battery_discharge_per_phase
                + ev_load[phase]
            )

        # Check each phase
        for phase in PHASES:
            phase_current = loads[phase] / self.voltage
            if phase_current > self.max_current:
                over_a = phase_current - self.max_current
                over_w = over_a * self.voltage
                _LOGGER.warning(
                    "Phase %s over limit: %.1fA (max %.1fA) – reducing loads by %.0fW",
                    phase, phase_current, self.max_current, over_w
                )

                # 1. Reduce EV current on that phase first
                if phase == state.ev_phase and decision.ev_enable:
                    reduce_a = min(over_a, decision.ev_current_a - MIN_EV_CURRENT)
                    if reduce_a > 0:
                        decision.ev_current_a -= reduce_a
                        loads[phase] -= reduce_a * self.voltage
                        over_w = max(0, (loads[phase] / self.voltage - self.max_current) * self.voltage)

                # 2. Reduce battery charge
                if over_w > 0 and decision.battery_charge_power_w > 0:
                    reduce_w = min(over_w * 3, decision.battery_charge_power_w)
                    decision.battery_charge_power_w -= reduce_w
                    for p in PHASES:
                        loads[p] -= reduce_w / 3.0
                    over_w = max(0, (loads[phase] / self.voltage - self.max_current) * self.voltage)

                # 3. Last resort: disable EV
                if over_w > 0 and decision.ev_enable and phase == state.ev_phase:
                    _LOGGER.warning("Disabling EV charging to stay within phase limit")
                    decision.ev_enable = False
                    decision.ev_current_a = 0
                    loads[phase] -= ev_load[phase]

        decision.phase_loads = PhaseLoad(
            L1=loads.get("L1", 0.0),
            L2=loads.get("L2", 0.0),
            L3=loads.get("L3", 0.0),
        )
        return decision

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------
    def _solar_to_ev_current(self, solar_surplus_w: float, ev_phase: str) -> float:
        """Convert solar surplus to EV current (1-phase car)."""
        current = solar_surplus_w / self.voltage
        current = max(MIN_EV_CURRENT, min(MAX_EV_CURRENT, current))
        return round(current)

    def calculate_buy_price(
        self,
        spot_price: float,
        grid_fees: float,
        energy_tax: float,
        vat_rate: float,
    ) -> float:
        """(spot + fees + tax) * (1 + vat)"""
        return (spot_price + grid_fees + energy_tax) * (1 + vat_rate)

    def calculate_sell_price(self, spot_price: float, extra_revenue: float) -> float:
        """spot + extra (elcertifikat etc)."""
        return spot_price + extra_revenue
