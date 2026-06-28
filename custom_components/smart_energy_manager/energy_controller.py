"""Core energy control logic for Smart Energy Manager."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from .const import (
    PHASES, DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE,
    MIN_EV_CURRENT, MAX_EV_CURRENT,
    MIN_SOLAR_FOR_EV_1PHASE, MIN_SOLAR_FOR_EV_3PHASE,
    NEGATIVE_PRICE_THRESHOLD,
    EV_PHASE_L1, EV_PHASE_L2, EV_PHASE_L3,
    MODE_AUTO, MODE_WINTER, MODE_FORCE_CHARGE_EV,
    MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL,
    DEFAULT_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PATRON_PHASES,
    DEFAULT_HEAT_PUMP_PATRON_POWER_KW,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PhaseLoad:
    """Estimated grid import per phase in Watts (positive = import)."""
    L1: float = 0.0
    L2: float = 0.0
    L3: float = 0.0

    def max_phase(self) -> float:
        return max(self.L1, self.L2, self.L3)

    def as_dict(self) -> dict:
        return {"L1": self.L1, "L2": self.L2, "L3": self.L3}


@dataclass
class EvCarConfig:
    """Configuration for a single EV / charging slot."""
    name: str
    charger_switch: str
    charger_current: str
    charger_power: Optional[str] = None
    ev_soc: Optional[str] = None
    ev_soc_target: float = 80.0
    phases: int = 1                   # 1 or 3
    phase: Optional[str] = EV_PHASE_L1  # only relevant when phases == 1


@dataclass
class EvCarState:
    """Runtime state for one EV car/charger."""
    config: EvCarConfig
    charging: bool = False
    current_a: float = 0.0
    power_w: float = 0.0
    soc_pct: Optional[float] = None


@dataclass
class EvCarDecision:
    """Control output for one EV car/charger."""
    enable: bool = False
    current_a: float = 0.0
    reason: str = ""


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
    battery_power_w: float = 0.0        # + = charging, – = discharging
    battery_capacity_kwh: float = 10.0
    battery_max_power_kw: float = 5.0

    # EV: list of per-car states (may be empty)
    ev_cars: list[EvCarState] = field(default_factory=list)

    # Heat pump
    # Compressor (always 1-phase, e.g. L3)
    heat_pump_power_w: float = 0.0
    heat_pump_on: bool = False
    heat_pump_phase: str = DEFAULT_HEAT_PUMP_PHASE
    # Heating element / patron (always 2-phase, e.g. L1+L2)
    extra_hot_water_on: bool = False
    heat_pump_patron_phases: list[str] = field(default_factory=lambda: list(DEFAULT_HEAT_PUMP_PATRON_PHASES))
    heat_pump_patron_power_kw: float = DEFAULT_HEAT_PUMP_PATRON_POWER_KW

    # Direkt huslastavläsning (Elm4) i W – 0.0 innebär att koordinatorn beräknar den
    # Elm4 täcker: övriga laster + elpanna (Elm5)
    # Elm4 inkluderar INTE billaddaren (bekräftat från mätdata: korr 0.98)
    house_load_w: float = 0.0

    # Legionella-desinficering aktiv – åsidosätter extra_hot_water i executor
    legionella_active: bool = False

    # Grid (positive = import from grid)
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
    # Per-car EV decisions (same order as EnergyState.ev_cars)
    ev_decisions: list[EvCarDecision] = field(default_factory=list)
    extra_hot_water: bool = False
    reason: str = ""
    phase_loads: PhaseLoad = field(default_factory=PhaseLoad)

    # Convenience: True if at least one car is enabled
    @property
    def any_ev_enabled(self) -> bool:
        return any(d.enable for d in self.ev_decisions)


class EnergyController:
    """
    Main energy control logic.

    Phase model
    ───────────
    Solar inverter:        3-phase, power split equally across L1/L2/L3
    Battery inverter:      3-phase, power split equally across L1/L2/L3
    EV charger hardware:   3-phase hardware, but each car configures independently:
                             - 1-phase car: full current drawn on one phase (L1/L2/L3)
                             - 3-phase car: current drawn equally on all three phases
    Heat pump compressor:  1-phase (configured phase, typically L3)
    Heating element/patron:2-phase (configured pair, typically L1+L2), equal per phase

    Priority (auto mode)
    ────────────────────
    1. Cover house load from solar
    2. Charge EVs from solar surplus
    3. Charge battery from remaining solar surplus
    4. Extra hot water when battery full and surplus remains
    5. Discharge battery to cover house load (when buy price > threshold)
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

    # ──────────────────────────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ──────────────────────────────────────────────────────────────────
    def compute(self, state: EnergyState) -> ControlDecision:
        """Compute control decisions based on current state."""
        # Initialise one EvCarDecision per car (all disabled by default)
        n_cars = len(state.ev_cars)
        decision = ControlDecision(
            ev_decisions=[EvCarDecision() for _ in range(n_cars)]
        )

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

    # ──────────────────────────────────────────────────────────────────
    # AUTO MODE
    # ──────────────────────────────────────────────────────────────────
    def _auto_mode(self, state: EnergyState) -> ControlDecision:
        """Self-consumption optimised control."""
        n_cars = len(state.ev_cars)
        decision = ControlDecision(
            reason="Auto mode",
            ev_decisions=[EvCarDecision() for _ in range(n_cars)],
        )

        solar_w = state.solar_power_w
        battery_soc = state.battery_soc_pct
        buy_price = state.buy_price_sek_kwh
        sell_price = state.sell_price_sek_kwh
        negative_price = sell_price < NEGATIVE_PRICE_THRESHOLD

        # Huslast – Elm4 direkt om konfigurerat, annars beräkna
        # Elm4 = övriga laster + elpanna (Elm5), EXKL. billaddare
        if state.house_load_w > 0:
            house_load_w = state.house_load_w
        else:
            house_load_w = max(0.0, (
                state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3
                + solar_w
                + max(0, -state.battery_power_w)
                - max(0, state.battery_power_w)
            ))

        # Tillgängligt solöverskott efter huslast
        solar_surplus_w = max(0.0, solar_w - house_load_w)

        _LOGGER.debug(
            "Auto: solar=%.0fW house=%.0fW surplus=%.0fW soc=%.0f%% buy=%.3f sell=%.3f neg=%s",
            solar_w, house_load_w, solar_surplus_w, battery_soc,
            buy_price, sell_price, negative_price,
        )

        # ── Negative spot price ───────────────────────────────────────
        if negative_price and solar_w > 0:
            decision.reason += " | Negative spot – absorbing solar"
            remaining = solar_surplus_w

            if battery_soc < self.battery_max_soc:
                charge_w = min(state.battery_max_power_kw * 1000, remaining)
                decision.battery_charge_power_w = charge_w
                remaining -= charge_w

            if remaining > 500:
                decision.extra_hot_water = True

            # Offer remaining surplus to each car in priority order
            for i, car in enumerate(state.ev_cars):
                min_surplus = (MIN_SOLAR_FOR_EV_1PHASE if car.config.phases == 1
                               else MIN_SOLAR_FOR_EV_3PHASE)
                if remaining >= min_surplus:
                    cur = self._surplus_to_ev_current(remaining, car.config)
                    decision.ev_decisions[i] = EvCarDecision(
                        enable=True,
                        current_a=cur,
                        reason="negative price – solar absorption",
                    )
                    remaining -= self._ev_power(cur, car.config)

            return self._apply_phase_limits(state, decision)

        # ── Normal auto ───────────────────────────────────────────────

        # Step 1: Charge EVs from solar surplus (priority: car order)
        remaining_surplus = solar_surplus_w
        for i, car in enumerate(state.ev_cars):
            if car.soc_pct is not None and car.soc_pct >= car.config.ev_soc_target:
                decision.ev_decisions[i].reason = f"SOC target reached ({car.soc_pct:.0f}%)"
                continue

            min_surplus = (MIN_SOLAR_FOR_EV_1PHASE if car.config.phases == 1
                           else MIN_SOLAR_FOR_EV_3PHASE)
            if remaining_surplus >= min_surplus:
                cur = self._surplus_to_ev_current(remaining_surplus, car.config)
                if cur >= MIN_EV_CURRENT:
                    consumed = self._ev_power(cur, car.config)
                    decision.ev_decisions[i] = EvCarDecision(
                        enable=True,
                        current_a=cur,
                        reason=f"solar charge {cur:.0f}A",
                    )
                    remaining_surplus = max(0.0, remaining_surplus - consumed)
                    decision.reason += f" | {car.config.name} {cur:.0f}A solar"

        # Step 2: Charge battery from remaining surplus
        if remaining_surplus > 100 and battery_soc < self.battery_max_soc:
            charge_w = min(state.battery_max_power_kw * 1000, remaining_surplus)
            decision.battery_charge_power_w = charge_w
            remaining_surplus -= charge_w
            decision.reason += f" | Battery +{charge_w:.0f}W"

        # Step 3: Extra hot water (battery full, still surplus)
        if remaining_surplus > 500 and battery_soc >= self.battery_max_soc:
            decision.extra_hot_water = True
            decision.reason += " | Extra hot water (battery full)"

        # Step 4: Discharge battery for house load when no solar
        if solar_w < house_load_w and battery_soc > self.battery_min_soc:
            deficit_w = house_load_w - solar_w
            discharge_w = min(state.battery_max_power_kw * 1000, deficit_w)
            if buy_price > 0.20:
                decision.battery_discharge_power_w = discharge_w
                decision.reason += f" | Battery -{discharge_w:.0f}W (cover load)"

        # Step 5: Battery at min SOC – stop discharging
        if battery_soc <= self.battery_min_soc:
            decision.battery_discharge_power_w = 0
            decision.reason += " | Battery at min SOC"

        return self._apply_phase_limits(state, decision)

    # ──────────────────────────────────────────────────────────────────
    # WINTER MODE
    # ──────────────────────────────────────────────────────────────────
    def _winter_mode(self, state: EnergyState) -> ControlDecision:
        n_cars = len(state.ev_cars)
        decision = ControlDecision(
            reason="Winter mode",
            ev_decisions=[EvCarDecision() for _ in range(n_cars)],
        )
        hour = datetime.now().hour
        buy_price = state.buy_price_sek_kwh
        soc = state.battery_soc_pct
        solar_w = state.solar_power_w

        is_cheap = buy_price <= self.winter_cheap_threshold
        is_expensive = buy_price >= self.winter_expensive_threshold
        is_night = hour < 6 or hour >= 23

        _LOGGER.debug(
            "Winter: hour=%d price=%.3f cheap=%s expensive=%s soc=%.0f%%",
            hour, buy_price, is_cheap, is_expensive, soc,
        )

        if is_cheap and (is_night or buy_price < 0.30):
            if soc < self.winter_max_soc:
                decision.battery_charge_power_w = state.battery_max_power_kw * 1000
                decision.reason += f" | Charge battery (cheap {buy_price:.3f} SEK)"
        elif is_expensive and soc > self.winter_min_soc:
            decision.battery_discharge_power_w = state.battery_max_power_kw * 1000
            decision.reason += f" | Discharge battery (expensive {buy_price:.3f} SEK)"
        elif solar_w > 100 and soc < self.winter_max_soc:
            if state.house_load_w > 0:
                house_load_w = state.house_load_w
            else:
                house_load_w = max(0.0,
                    state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3 + solar_w
                )
            surplus = max(0.0, solar_w - house_load_w)
            if surplus > 100:
                decision.battery_charge_power_w = min(
                    state.battery_max_power_kw * 1000, surplus
                )
                decision.reason += " | Charge battery from solar (winter)"

        # EV: endast solöverskott i vinterläge
        _house = state.house_load_w if state.house_load_w > 0 else max(0.0,
            state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3 + solar_w
        )
        remaining_surplus = max(0.0, solar_w - _house)
        for i, car in enumerate(state.ev_cars):
            min_surplus = (MIN_SOLAR_FOR_EV_1PHASE if car.config.phases == 1
                           else MIN_SOLAR_FOR_EV_3PHASE)
            if remaining_surplus >= min_surplus:
                cur = self._surplus_to_ev_current(remaining_surplus, car.config)
                if cur >= MIN_EV_CURRENT:
                    decision.ev_decisions[i] = EvCarDecision(
                        enable=True,
                        current_a=cur,
                        reason="solar charge (winter)",
                    )
                    remaining_surplus -= self._ev_power(cur, car.config)

        return self._apply_phase_limits(state, decision)

    # ──────────────────────────────────────────────────────────────────
    # FORCE MODES
    # ──────────────────────────────────────────────────────────────────
    def _force_charge_ev(self, state: EnergyState) -> ControlDecision:
        """Force ALL EVs to charge from grid at max allowed current."""
        n_cars = len(state.ev_cars)
        decision = ControlDecision(
            reason="Force charge EVs from grid",
            ev_decisions=[
                EvCarDecision(enable=True, current_a=MAX_EV_CURRENT, reason="forced")
                for _ in range(n_cars)
            ],
        )
        return self._apply_phase_limits(state, decision)

    def _force_charge_battery(self, state: EnergyState) -> ControlDecision:
        n_cars = len(state.ev_cars)
        decision = ControlDecision(
            reason="Force charge battery from grid",
            ev_decisions=[EvCarDecision() for _ in range(n_cars)],
        )
        if state.battery_soc_pct < self.battery_max_soc:
            decision.battery_charge_power_w = state.battery_max_power_kw * 1000
        return self._apply_phase_limits(state, decision)

    # ──────────────────────────────────────────────────────────────────
    # PHASE LIMIT ENFORCEMENT
    # ──────────────────────────────────────────────────────────────────
    def _apply_phase_limits(self, state: EnergyState, decision: ControlDecision) -> ControlDecision:
        """
        Enforce max 20 A per phase.

        Phase contribution model
        ────────────────────────
        Solar:              –W/3 on each phase  (reduces import)
        Battery discharge:  –W/3 on each phase
        Battery charge:     +W/3 on each phase
        1-phase EV:         +current×V on its configured phase only
        3-phase EV:         +current×V on each of L1, L2, L3
        HP compressor:      +W on its configured 1 phase (e.g. L3)
        HP patron (2-phase):+W/2 on each of the two configured phases (e.g. L1, L2)
        """
        solar_per_phase = state.solar_power_w / 3.0
        batt_discharge_per_phase = decision.battery_discharge_power_w / 3.0
        batt_charge_per_phase = decision.battery_charge_power_w / 3.0

        # Initialise phase loads from grid readings (house loads already reflected)
        loads: dict[str, float] = {
            "L1": state.grid_power_l1,
            "L2": state.grid_power_l2,
            "L3": state.grid_power_l3,
        }

        # Apply solar and battery delta (vs. what's already in grid readings)
        for ph in PHASES:
            loads[ph] += (
                - solar_per_phase
                + batt_charge_per_phase
                - batt_discharge_per_phase
            )

        # ── EV loads ─────────────────────────────────────────────────
        ev_phase_loads: list[dict[str, float]] = []
        for i, (car, ev_dec) in enumerate(zip(state.ev_cars, decision.ev_decisions)):
            ph_load: dict[str, float] = {p: 0.0 for p in PHASES}
            if ev_dec.enable and ev_dec.current_a > 0:
                if car.config.phases == 3:
                    per_phase_w = ev_dec.current_a * self.voltage
                    for ph in PHASES:
                        ph_load[ph] = per_phase_w
                else:
                    phase = car.config.phase or "L1"
                    ph_load[phase] = ev_dec.current_a * self.voltage
            ev_phase_loads.append(ph_load)
            for ph in PHASES:
                loads[ph] += ph_load[ph]

        # ── Heat pump compressor (1-phase) ────────────────────────────
        if state.heat_pump_on:
            hp_ph = state.heat_pump_phase
            loads[hp_ph] = loads.get(hp_ph, 0.0) + state.heat_pump_power_w

        # ── Heating element/patron (2-phase) ─────────────────────────
        if decision.extra_hot_water:
            patron_phases = state.heat_pump_patron_phases or DEFAULT_HEAT_PUMP_PATRON_PHASES
            patron_total_w = state.heat_pump_patron_power_kw * 1000
            patron_per_phase_w = patron_total_w / len(patron_phases)
            for ph in patron_phases:
                loads[ph] = loads.get(ph, 0.0) + patron_per_phase_w

        # ── Enforce limits ────────────────────────────────────────────
        for iteration in range(4):   # max 4 reduction passes
            any_violation = False
            for ph in PHASES:
                phase_current = loads[ph] / self.voltage
                if phase_current <= self.max_current:
                    continue

                any_violation = True
                over_a = phase_current - self.max_current
                over_w = over_a * self.voltage

                _LOGGER.warning(
                    "Pass %d: Phase %s over limit %.1f A (max %.1f A) – reducing by %.0f W",
                    iteration, ph, phase_current, self.max_current, over_w,
                )

                # Reduction priority 1: EVs on this phase (lowest-priority car first)
                for i in range(len(state.ev_cars) - 1, -1, -1):
                    if over_w <= 0:
                        break
                    car = state.ev_cars[i]
                    ev_dec = decision.ev_decisions[i]
                    if not ev_dec.enable:
                        continue

                    if car.config.phases == 3:
                        # 3-phase car: reduce current affects all 3 phases equally
                        if ph not in PHASES:
                            continue
                        max_reduce_a = ev_dec.current_a - MIN_EV_CURRENT
                        if max_reduce_a <= 0:
                            # Can't reduce further – disable
                            ev_dec.enable = False
                            ev_dec.current_a = 0
                            for p in PHASES:
                                loads[p] -= ev_phase_loads[i][p]
                                ev_phase_loads[i][p] = 0
                            over_w = max(0, (loads[ph] / self.voltage - self.max_current) * self.voltage)
                        else:
                            reduce_a = min(max_reduce_a, over_w / self.voltage)
                            ev_dec.current_a -= reduce_a
                            for p in PHASES:
                                delta = reduce_a * self.voltage
                                loads[p] -= delta
                                ev_phase_loads[i][p] -= delta
                            over_w = max(0, (loads[ph] / self.voltage - self.max_current) * self.voltage)
                    else:
                        # 1-phase car: only applies when the car's phase == violating phase
                        if car.config.phase != ph:
                            continue
                        max_reduce_a = ev_dec.current_a - MIN_EV_CURRENT
                        if max_reduce_a <= 0:
                            # Disable the car entirely
                            ev_dec.enable = False
                            ev_dec.current_a = 0
                            loads[ph] -= ev_phase_loads[i][ph]
                            ev_phase_loads[i][ph] = 0
                        else:
                            reduce_a = min(max_reduce_a, over_w / self.voltage)
                            ev_dec.current_a -= reduce_a
                            delta = reduce_a * self.voltage
                            loads[ph] -= delta
                            ev_phase_loads[i][ph] -= delta
                        over_w = max(0, (loads[ph] / self.voltage - self.max_current) * self.voltage)

                # Reduction priority 2: battery charging (affects all 3 phases equally)
                if over_w > 0 and decision.battery_charge_power_w > 0:
                    reduce_w_total = min(over_w * 3, decision.battery_charge_power_w)
                    decision.battery_charge_power_w -= reduce_w_total
                    for p in PHASES:
                        loads[p] -= reduce_w_total / 3.0
                    over_w = max(0, (loads[ph] / self.voltage - self.max_current) * self.voltage)

                # Reduction priority 3: extra hot water (patron, 2-phase)
                if over_w > 0 and decision.extra_hot_water:
                    patron_phases = state.heat_pump_patron_phases or DEFAULT_HEAT_PUMP_PATRON_PHASES
                    if ph in patron_phases:
                        patron_total_w = state.heat_pump_patron_power_kw * 1000
                        patron_per_phase = patron_total_w / len(patron_phases)
                        for pp in patron_phases:
                            loads[pp] -= patron_per_phase
                        decision.extra_hot_water = False
                        _LOGGER.warning("Disabled extra hot water to stay within phase limit on %s", ph)
                        over_w = max(0, (loads[ph] / self.voltage - self.max_current) * self.voltage)

            if not any_violation:
                break

        decision.phase_loads = PhaseLoad(
            L1=loads.get("L1", 0.0),
            L2=loads.get("L2", 0.0),
            L3=loads.get("L3", 0.0),
        )
        return decision

    # ──────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────
    def _surplus_to_ev_current(self, surplus_w: float, car_cfg: EvCarConfig) -> float:
        """Convert available solar surplus to a feasible EV current setpoint."""
        if car_cfg.phases == 3:
            current = surplus_w / (self.voltage * 3)
        else:
            current = surplus_w / self.voltage
        return float(min(MAX_EV_CURRENT, max(MIN_EV_CURRENT, round(current))))

    def _ev_power(self, current_a: float, car_cfg: EvCarConfig) -> float:
        """Total power draw of a car charging at given current."""
        return current_a * self.voltage * car_cfg.phases

    def calculate_buy_price(
        self,
        spot_price: float,
        grid_fees: float,
        energy_tax: float,
        vat_rate: float,
    ) -> float:
        return (spot_price + grid_fees + energy_tax) * (1 + vat_rate)

    def calculate_sell_price(self, spot_price: float, extra_revenue: float) -> float:
        return spot_price + extra_revenue