"""Core energy control logic for Smart Energy Manager."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta, timezone

from .price_scheduler import PriceSchedule
from .const import (
    PHASES, DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE,
    MIN_EV_CURRENT, MAX_EV_CURRENT,
    MIN_SOLAR_FOR_EV_1PHASE, MIN_SOLAR_FOR_EV_3PHASE,
    NEGATIVE_PRICE_THRESHOLD,
    EV_PHASE_L1, NO_CAR_SELECTED,
    MODE_AUTO, MODE_WINTER, MODE_FORCE_CHARGE_EV,
    MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL,
    DEFAULT_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PATRON_PHASES,
    DEFAULT_HEAT_PUMP_PATRON_POWER_KW,
    DEFAULT_EXPORT_SELL_PERCENTILE, DEFAULT_EXPORT_MIN_SOLAR_TOMORROW_KWH,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PhaseLoad:
    L1: float = 0.0
    L2: float = 0.0
    L3: float = 0.0

    def max_phase(self) -> float:
        return max(self.L1, self.L2, self.L3)

    def as_dict(self) -> dict:
        return {"L1": self.L1, "L2": self.L2, "L3": self.L3}


# ── Laddare-dataklasser ────────────────────────────────────────────────────────

@dataclass
class CarConfig:
    """Konfiguration för en bil som kan använda en laddare."""
    name: str
    ev_soc: Optional[str] = None
    ev_soc_target: float = 80.0
    # Bilens inbyggda laddare: 1, 2 eller 3 faser
    car_phases: int = 1
    # Startfas – vid 1-fas: den enda fasen; vid 2-fas: första fasen (nästa fas = fas+1)
    phase: Optional[str] = EV_PHASE_L1


@dataclass
class ChargerConfig:
    """Statisk konfiguration för en EV-laddare."""
    name: str
    charger_switch: str
    charger_current: str
    connected_sensor: Optional[str] = None
    charger_power: Optional[str] = None
    phases: int = 1
    phase: Optional[str] = EV_PHASE_L1   # laddarhårdvarans fas (vid 1-fas)
    cars: list[CarConfig] = field(default_factory=list)


@dataclass
class ChargerState:
    """Körtidsstatus för en laddare."""
    config: ChargerConfig
    # Anslutningsstatus från sensor
    connected: bool = False
    # Namn på vald bil ("unknown" = ingen vald ännu)
    active_car_name: str = NO_CAR_SELECTED
    # Faktisk laddström just nu
    current_a: float = 0.0
    # Faktisk effekt
    power_w: float = 0.0
    # SOC för aktiv bil (None om okänd)
    soc_pct: Optional[float] = None

    @property
    def active_car(self) -> Optional[CarConfig]:
        """Returnera CarConfig för vald bil, eller None om okänd."""
        if self.active_car_name == NO_CAR_SELECTED:
            return None
        for car in self.config.cars:
            if car.name == self.active_car_name:
                return car
        return None

    @property
    def soc_target(self) -> float:
        car = self.active_car
        return car.ev_soc_target if car else 80.0

    @property
    def effective_phases(self) -> list[str]:
        """
        Returnera lista av faser som bilen faktiskt laddar på.
        Bestäms av bilens inbyggda laddare (car_phases), inte laddarhårdvaran.
          1-fas bil: [car.phase]
          2-fas bil: [car.phase, nästa fas]   t.ex. L1 → [L1, L2]
          3-fas bil: [L1, L2, L3]
        """
        car = self.active_car
        car_phases = car.car_phases if car else 1
        start_phase = (car.phase if car and car.phase else self.config.phase) or "L1"

        if car_phases >= 3:
            return ["L1", "L2", "L3"]
        elif car_phases == 2:
            phase_order = ["L1", "L2", "L3"]
            idx = phase_order.index(start_phase) if start_phase in phase_order else 0
            return [phase_order[idx], phase_order[(idx + 1) % 3]]
        else:
            return [start_phase]

    @property
    def effective_phase(self) -> Optional[str]:
        """Bakåtkompatibilitet – returnerar första fasen eller None vid 3-fas."""
        phases = self.effective_phases
        return phases[0] if len(phases) == 1 else None

    @property
    def car_phases(self) -> int:
        """Antal faser bilens inbyggda laddare använder."""
        car = self.active_car
        return car.car_phases if car else 1


@dataclass
class ChargerDecision:
    """Styrningsbeslut för en laddare."""
    enable: bool = False
    current_a: float = 0.0
    reason: str = ""
    # True om laddaren är ansluten men ingen bil är vald → blockera + notifiera
    needs_car_selection: bool = False


# ── Övrig systemstat ──────────────────────────────────────────────────────────

@dataclass
class EnergyState:
    """Aktuellt tillstånd för hela energisystemet."""
    # Solar
    solar_power_w: float = 0.0
    solar_power_l1: float = 0.0
    solar_power_l2: float = 0.0
    solar_power_l3: float = 0.0
    solar_forecast_today_kwh: float = 0.0
    solar_forecast_tomorrow_kwh: float = 0.0

    # Batteri
    battery_soc_pct: float = 50.0
    battery_power_w: float = 0.0
    battery_capacity_kwh: float = 10.0
    battery_max_power_kw: float = 5.0

    # EV-laddare (ny modell)
    chargers: list[ChargerState] = field(default_factory=list)

    # Elpanna
    heat_pump_power_w: float = 0.0
    heat_pump_phase: str = DEFAULT_HEAT_PUMP_PHASE
    extra_hot_water_on: bool = False
    heat_pump_patron_phases: list[str] = field(default_factory=lambda: list(DEFAULT_HEAT_PUMP_PATRON_PHASES))
    heat_pump_patron_power_kw: float = DEFAULT_HEAT_PUMP_PATRON_POWER_KW

    # Huslast
    house_load_w: float = 0.0

    # Varmvattentemperatur (ackumulatortank)
    hot_water_temp_c: Optional[float] = None      # None om sensor ej konfigurerad
    extra_hot_water_max_temp: float = 70.0        # stoppa extra varmvatten över detta (°C)
    extra_hot_water_min_temp: float = 65.0        # starta inte extra varmvatten förrän temp är under detta (°C)

    # Legionella
    legionella_active: bool = False

    # Nät
    grid_power_l1: float = 0.0
    grid_power_l2: float = 0.0
    grid_power_l3: float = 0.0
    grid_current_l1: float = 0.0
    grid_current_l2: float = 0.0
    grid_current_l3: float = 0.0

    # Priser
    spot_price_sek_kwh: float = 0.0
    buy_price_sek_kwh: float = 0.0
    sell_price_sek_kwh: float = 0.0

    # Prisschema (kvartstimmar framåt från Nordpool)
    price_schedule: Optional[PriceSchedule] = None

    # Gårdagens förbrukning exkl. EV-laddning (kWh)
    yesterday_consumption_kwh: Optional[float] = None

    # Utomhustemperatur (°C) – aktuell mätning
    outdoor_temp_c: Optional[float] = None

    # Gårdagens dygnsmedeltemperatur (°C) – mer stabil indata för prognosen
    avg_temp_yesterday_c: Optional[float] = None

    # Sant om desinficering/legionella pågår just nu
    disinfecting_active: bool = False

    # Beräknad daglig förbrukning (kWh) från temperaturmodell
    # base_dhw + k * max(0, T_balance - avg_temp) [+ extra om disinfecting]
    predicted_daily_kwh: float = 0.0

    # Sol-tider (från HA sun-integration, används för dynamisk kvällsfylling)
    sun_next_setting: Optional[datetime] = None
    sun_next_rising: Optional[datetime] = None

    # Tidpunkt då sol förväntas täcka huslasten (beräknat från Solcast imorgon)
    solar_takeover_dt: Optional[datetime] = None

    # Driftläge
    operating_mode: str = MODE_AUTO
    winter_mode: bool = False


@dataclass
class ControlDecision:
    """Styrningsbeslut för ett kontrollcykel."""
    battery_charge_power_w: float = 0.0
    battery_discharge_power_w: float = 0.0
    charger_decisions: list[ChargerDecision] = field(default_factory=list)
    extra_hot_water: bool = False
    reason: str = ""
    phase_loads: PhaseLoad = field(default_factory=PhaseLoad)
    # Laddare som behöver bilval (för notifieringar)
    chargers_needing_selection: list[str] = field(default_factory=list)

    @property
    def any_ev_enabled(self) -> bool:
        return any(d.enable for d in self.charger_decisions)

    # Bakåtkompatibilitet med sensor.py som läser ev_decisions
    @property
    def ev_decisions(self):
        return self.charger_decisions


class EnergyController:
    """
    Huvudstyrlogik för Smart Energy Manager.

    Fas-modell
    ──────────
    Sol-inverter:       3-fas, lika fördelat L1/L2/L3
    Batteri-inverter:   3-fas, lika fördelat L1/L2/L3
    1-fas laddare:      ström på bilens konfigurerade fas
    3-fas laddare:      lika ström på alla tre faser
    Värmepump:          1-fas (konfigurerbar)
    Elpatron:           2-fas (de två återstående faserna)

    Prioritet (autoläge)
    ────────────────────
    1. Täck huslast från sol
    2. Ladda anslutna bilar från solöverskott
    3. Ladda batteri från återstående överskott
    4. Extra varmvatten när batteri fullt och överskott kvar
    5. Ladda ur batteri för huslast (när köppris > tröskel)
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
        auto_discharge_threshold_sek: float = 0.20,
        sell_solar_min_price_sek: float = 0.80,
        evening_min_soc: float = 90.0,
        export_sell_percentile: float = DEFAULT_EXPORT_SELL_PERCENTILE,
        export_min_solar_tomorrow_kwh: float = DEFAULT_EXPORT_MIN_SOLAR_TOMORROW_KWH,
    ):
        self.max_current = max_current_per_phase
        self.voltage = grid_voltage
        self.max_phase_power = max_current_per_phase * grid_voltage
        self.battery_min_soc = battery_min_soc
        self.battery_max_soc = battery_max_soc
        self.ev_soc_target = ev_soc_target
        self.winter_cheap_threshold = winter_cheap_threshold
        self.winter_expensive_threshold = winter_expensive_threshold
        self.winter_min_soc = winter_min_soc
        self.winter_max_soc = winter_max_soc
        self.auto_discharge_threshold = auto_discharge_threshold_sek
        self.sell_solar_min_price = sell_solar_min_price_sek
        self.evening_min_soc = evening_min_soc
        self.export_sell_percentile = export_sell_percentile
        self.export_min_solar_tomorrow_kwh = export_min_solar_tomorrow_kwh

    # ── Publik ingångspunkt ───────────────────────────────────────────

    def compute(self, state: EnergyState) -> ControlDecision:
        n = len(state.chargers)
        decision = ControlDecision(
            charger_decisions=[ChargerDecision() for _ in range(n)]
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

    # ── Bilvalskontroll ───────────────────────────────────────────────

    def _check_car_selection(
        self, state: EnergyState, decision: ControlDecision
    ) -> None:
        """
        Markera laddare som är anslutna men saknar bilval.
        Dessa blockeras från laddning och läggs i chargers_needing_selection.
        """
        for i, (ch, dec) in enumerate(zip(state.chargers, decision.charger_decisions)):
            if ch.connected and ch.active_car_name == NO_CAR_SELECTED:
                dec.enable = False
                dec.current_a = 0.0
                dec.needs_car_selection = True
                dec.reason = f"{ch.config.name}: ansluten men ingen bil vald"
                decision.chargers_needing_selection.append(ch.config.name)

    # ── Auto-läge ─────────────────────────────────────────────────────

    def _auto_mode(self, state: EnergyState) -> ControlDecision:
        n = len(state.chargers)
        decision = ControlDecision(
            reason="Auto mode",
            charger_decisions=[ChargerDecision() for _ in range(n)],
        )

        solar_w = state.solar_power_w
        battery_soc = state.battery_soc_pct
        buy_price = state.buy_price_sek_kwh
        sell_price = state.sell_price_sek_kwh
        negative_price = sell_price < NEGATIVE_PRICE_THRESHOLD

        house_load_w = self._house_load(state)
        solar_surplus_w = max(0.0, solar_w - house_load_w)

        _LOGGER.debug(
            "Auto: solar=%.0fW house=%.0fW surplus=%.0fW soc=%.0f%% buy=%.3f neg=%s",
            solar_w, house_load_w, solar_surplus_w, battery_soc, buy_price, negative_price,
        )

        # ── Proaktiv absorption: negativa priser väntar inom 2h ─────
        # Håller headroom i batteriet men startar INTE varmvatten eller EV
        # i förväg — de väntar till priset faktiskt är negativt.
        ps = state.price_schedule
        had_negative_today = ps is not None and ps.negative_slots_passed_today > 0
        if ps and ps.should_absorb_proactively and not negative_price:
            decision.reason += f" | Headroom inför neg pris ({ps.negative_slots_ahead} slots inom 8h)"
            effective_max_soc = self.battery_max_soc - (ps.recommended_headroom * 100)
            if battery_soc > effective_max_soc:
                decision.battery_charge_power_w = 0
                decision.reason += f" | Håller {ps.recommended_headroom*100:.0f}% headroom"
            self._check_car_selection(state, decision)
            return self._apply_phase_limits(state, decision)

        # ── Negativa spotpriser just nu ───────────────────────────────
        if negative_price and solar_w > 0:
            decision.reason += " | Negative spot – absorbing solar"
            remaining = solar_surplus_w

            if battery_soc < self.battery_max_soc:
                charge_w = min(state.battery_max_power_kw * 1000, remaining)
                decision.battery_charge_power_w = charge_w
                remaining -= charge_w

            if remaining > 500 and self._can_start_extra_hot_water(state):
                decision.extra_hot_water = True

            for i, ch in enumerate(state.chargers):
                if not ch.connected or ch.active_car_name == NO_CAR_SELECTED:
                    continue
                min_surplus = MIN_SOLAR_FOR_EV_1PHASE if ch.car_phases == 1 else MIN_SOLAR_FOR_EV_3PHASE
                if remaining >= min_surplus:
                    cur = self._surplus_to_current(remaining, ch.car_phases)
                    decision.charger_decisions[i] = ChargerDecision(
                        enable=True, current_a=cur,
                        reason="negative price – solar absorption",
                    )
                    remaining -= self._charger_power(cur, ch.car_phases)

            self._check_car_selection(state, decision)
            return self._apply_phase_limits(state, decision)

        # ── Normal auto ───────────────────────────────────────────────
        remaining_surplus = solar_surplus_w

        for i, ch in enumerate(state.chargers):
            if not ch.connected:
                continue
            if ch.active_car_name == NO_CAR_SELECTED:
                continue
            car = ch.active_car
            if car and ch.soc_pct is not None and ch.soc_pct >= car.ev_soc_target:
                decision.charger_decisions[i].reason = f"SOC mål nått ({ch.soc_pct:.0f}%)"
                continue

            min_surplus = MIN_SOLAR_FOR_EV_1PHASE if ch.car_phases == 1 else MIN_SOLAR_FOR_EV_3PHASE
            if remaining_surplus >= min_surplus:
                cur = self._surplus_to_current(remaining_surplus, ch.car_phases)
                if cur >= MIN_EV_CURRENT:
                    consumed = self._charger_power(cur, ch.car_phases)
                    decision.charger_decisions[i] = ChargerDecision(
                        enable=True, current_a=cur,
                        reason=f"solladdar {cur:.0f}A",
                    )
                    remaining_surplus = max(0.0, remaining_surplus - consumed)
                    decision.reason += f" | {ch.config.name} {cur:.0f}A sol"

        # Batteriladdning från solöverskott – tre styrfaktorer:
        #
        # 1. Kvällsfylling: efter evening_fill_hour, fyll alltid batteriet
        #    inför natten (solar_w > 0 = solen fortfarande uppe).
        # 2. Säljpris: om säljpris är högt, exportera hellre än att lagra.
        # 3. Vänta på sol: om solen knappt producerar men stor sol väntas,
        #    håll plats i batteriet.
        ps = state.price_schedule
        now_aware = datetime.now().astimezone()

        # Kvällsfylling: Solcast-prognosen för platsen avgör om solen räcker
        # för att fylla batteriet innan solnedgång.
        #
        # Logik: summera förväntad solenergi (kWh) från Solcast-slots fram till
        # solnedgång. Om den summan understiger vad batteriet behöver för att nå
        # evening_min_soc → starta kvällsfylling nu, oavsett säljpris.
        #
        # Fungerar automatiskt för alla årstider eftersom Solcast känner till
        # exakt panelvinkel och plats.
        solar_until_sunset_kwh = 0.0
        sun_set: Optional[datetime] = None
        if state.sun_next_setting is not None:
            sun_set = state.sun_next_setting
            if sun_set.tzinfo is None:
                sun_set = sun_set.astimezone()
            if ps and ps.slots:
                solar_until_sunset_kwh = sum(
                    s.solar_kwh for s in ps.slots
                    if s.end > now_aware and s.start < sun_set
                )

        # Dynamiskt kvällsmål: beräkna hur mycket energi som behövs för natten.
        #
        # Om predicted_daily_kwh finns (temperaturmodell): räkna ut timmar tills
        # solen producerar tillräckligt för att täcka huslasten imorgon bitti.
        # Annars: fall tillbaka på fast evening_min_soc.
        evening_target_soc = self.evening_min_soc
        evening_needed_kwh = 0.0

        if state.predicted_daily_kwh > 0 and ps and ps.slots:
            hourly_load_kw = state.predicted_daily_kwh / 24.0
            # Hitta första slot imorgon där soleffekten täcker huslasten
            solar_covers_at: Optional[datetime] = None
            for slot in ps.slots:
                if slot.start > now_aware and slot.solar_kw >= hourly_load_kw:
                    solar_covers_at = slot.start
                    break

            if solar_covers_at is None and state.sun_next_rising:
                # Solcast saknar data bortom idag – uppskatta 3h efter soluppgång
                rising = state.sun_next_rising
                if rising.tzinfo is None:
                    rising = rising.astimezone()
                solar_covers_at = rising + timedelta(hours=3)

            if solar_covers_at is not None:
                hours_dark = max(0.0, (solar_covers_at - now_aware).total_seconds() / 3600)
                evening_needed_kwh = hourly_load_kw * hours_dark + 2.0  # +2 kWh laddmarginal
                evening_target_soc = min(
                    self.battery_max_soc,
                    evening_needed_kwh / state.battery_capacity_kwh * 100.0,
                )
                _LOGGER.debug(
                    "Kvällsfylling dynamisk: %.1f kWh behövs (%.1fh mörker) → mål %.0f%% SOC",
                    evening_needed_kwh, hours_dark, evening_target_soc,
                )

        battery_remaining_kwh = (
            state.battery_capacity_kwh
            * max(0.0, evening_target_soc - battery_soc)
            / 100.0
        )

        evening_fill = (
            sun_set is not None
            and solar_w > 100                              # sol fortfarande igång
            and battery_soc < evening_target_soc
            and solar_until_sunset_kwh < battery_remaining_kwh  # prognosen räcker inte
        )

        _LOGGER.debug(
            "Kvällsfylling: sol_kvar=%.1f kWh batteri_kvar=%.1f kWh mål=%.0f%% → %s",
            solar_until_sunset_kwh, battery_remaining_kwh, evening_target_soc, evening_fill,
        )

        # Föredrar export om säljpriset är högt – men aldrig under kvällsfylling.
        prefer_sell = (
            sell_price >= self.sell_solar_min_price
            and not evening_fill
        )

        # Vänta på sol: solen inte igång än men stor sol väntas.
        # Gäller inte under kvällsfylling.
        wait_solar = (
            ps is not None
            and ps.should_wait_for_solar
            and solar_w < 500
            and not evening_fill
        )

        if remaining_surplus > 100 and battery_soc < self.battery_max_soc:
            if wait_solar:
                decision.reason += (
                    f" | Väntar på sol ({ps.solar_next_2h_kwh:.1f} kWh inom 2h)"
                )
            elif prefer_sell:
                decision.reason += (
                    f" | Exporterar sol (sälj {sell_price:.2f} kr/kWh)"
                )
            else:
                charge_w = min(state.battery_max_power_kw * 1000, remaining_surplus)
                decision.battery_charge_power_w = charge_w
                remaining_surplus -= charge_w
                if evening_fill:
                    suffix = (
                        f" (kvällsfylling: {solar_until_sunset_kwh:.1f} kWh "
                        f"sol kvar < {battery_remaining_kwh:.1f} kWh behövs)"
                    )
                else:
                    suffix = ""
                decision.reason += f" | Batteri +{charge_w:.0f}W{suffix}"

        # Extra varmvatten – batteri fullt och solöverskott, eller vi har passerat negativt pris idag
        varmvatten_ok = (
            (remaining_surplus > 500 and battery_soc >= self.battery_max_soc)
            or had_negative_today
        )
        if varmvatten_ok and self._can_start_extra_hot_water(state):
            decision.extra_hot_water = True
            temp_str = f" (tank {state.hot_water_temp_c:.0f}°C)" if state.hot_water_temp_c is not None else ""
            trigger = "passerat neg pris" if had_negative_today and not (remaining_surplus > 500 and battery_soc >= self.battery_max_soc) else "batteri fullt"
            decision.reason += f" | Extra varmvatten ({trigger}{temp_str})"

        # ── Proaktiv export: sälj dyrt, fyll på med sol imorgon ─────────
        # Villkor:
        #   1. Aktuellt säljpris ≥ export_sell_percentile av dagens alla priser
        #   2. Solcast imorgon ≥ export_min_solar_tomorrow_kwh (vi kan ladda igen)
        #   3. Batteri > nattens energibehov + 2 kWh marginal (täcker huset tills sol tar över)
        #   4. Batteri > battery_min_soc (absolut golv oavsett)
        _now_utc = datetime.now(timezone.utc)
        _ref_dt = state.solar_takeover_dt or state.sun_next_rising
        if _ref_dt and _ref_dt > _now_utc:
            _hours_dark = (_ref_dt - _now_utc).total_seconds() / 3600.0
        else:
            _hours_dark = 0.0
        _export_floor_kwh = _hours_dark * (house_load_w / 1000.0) + 2.0
        _battery_energy_kwh = battery_soc / 100.0 * state.battery_capacity_kwh

        export_active = False
        if (
            ps and ps.slots
            and battery_soc > self.battery_min_soc
            and _battery_energy_kwh > _export_floor_kwh
            and state.solar_forecast_tomorrow_kwh >= self.export_min_solar_tomorrow_kwh
        ):
            today_sell_prices = sorted(s.sell_sek for s in ps.slots)
            if today_sell_prices:
                idx = int(self.export_sell_percentile * len(today_sell_prices))
                idx = min(idx, len(today_sell_prices) - 1)
                price_threshold = today_sell_prices[idx]
                if sell_price >= price_threshold:
                    export_active = True

                    # Modulera effekten: dela exporterbar energi jämnt över
                    # återstående höga prisslotar (sell_sek >= tröskeln).
                    _now_local = datetime.now().astimezone()
                    _high_slots = [
                        s for s in ps.slots
                        if s.sell_sek >= price_threshold and s.end > _now_local
                    ]
                    _high_hours = sum(
                        (s.end - max(s.start, _now_local)).total_seconds() / 3600.0
                        for s in _high_slots
                    )
                    _exportable_kwh = _battery_energy_kwh - _export_floor_kwh
                    if _high_hours > 0.25:
                        _target_w = (_exportable_kwh / _high_hours) * 1000.0
                    else:
                        _target_w = state.battery_max_power_kw * 1000.0
                    discharge_w = max(500.0, min(_target_w, state.battery_max_power_kw * 1000.0))

                    decision.battery_discharge_power_w = discharge_w
                    decision.reason += (
                        f" | Proaktiv export {sell_price:.2f} kr/kWh"
                        f" (≥{self.export_sell_percentile*100:.0f}:e percentil {price_threshold:.2f})"
                        f" sol imorgon {state.solar_forecast_tomorrow_kwh:.1f} kWh"
                        f" golv {_export_floor_kwh:.1f} kWh ({_hours_dark:.1f}h mörker)"
                        f" {discharge_w:.0f}W/{_high_hours:.1f}h"
                    )
                    _LOGGER.info(
                        "Proaktiv export: %.0f W (%.1f kWh / %.1fh) säljpris %.3f ≥ %.3f kr/kWh"
                        " | batteri %.1f kWh > golv %.1f kWh",
                        discharge_w, _exportable_kwh, _high_hours,
                        sell_price, price_threshold,
                        _battery_energy_kwh, _export_floor_kwh,
                    )

        # Ladda ur batteri för att täcka huslast (om inte proaktiv export redan satt urladdningen)
        if not export_active and solar_w < house_load_w and battery_soc > self.battery_min_soc:
            deficit_w = house_load_w - solar_w
            discharge_w = min(state.battery_max_power_kw * 1000, deficit_w)
            now = datetime.now().astimezone()
            # Ladda ur om: priset är tillräckligt högt ELLER detta är bästa timmen kommande 12h
            is_good_discharge = buy_price > self.auto_discharge_threshold
            if ps and ps.best_discharge_slot:
                is_peak_now = abs((ps.best_discharge_slot.start - now).total_seconds()) < 900
                if is_peak_now:
                    is_good_discharge = True
                    decision.reason += " | Bästa urladdningstimmen"
            if is_good_discharge:
                decision.battery_discharge_power_w = discharge_w
                decision.reason += f" | Batteri -{discharge_w:.0f}W"

        if battery_soc <= self.battery_min_soc:
            decision.battery_discharge_power_w = 0
            decision.reason += " | Batteri vid min SOC"

        self._check_car_selection(state, decision)
        return self._apply_phase_limits(state, decision)

    # ── Vinterläge ────────────────────────────────────────────────────

    def _winter_mode(self, state: EnergyState) -> ControlDecision:
        n = len(state.chargers)
        decision = ControlDecision(
            reason="Winter mode",
            charger_decisions=[ChargerDecision() for _ in range(n)],
        )
        hour = datetime.now().hour
        buy_price = state.buy_price_sek_kwh
        soc = state.battery_soc_pct
        solar_w = state.solar_power_w

        is_cheap = buy_price <= self.winter_cheap_threshold
        is_expensive = buy_price >= self.winter_expensive_threshold
        is_night = hour < 6 or hour >= 23

        # Prisschema: är nu det bästa laddningstillfället kommande 12h?
        ps = state.price_schedule
        now = datetime.now().astimezone()
        is_best_charge_now = (
            ps is not None
            and ps.best_charge_slot is not None
            and abs((ps.best_charge_slot.start - now).total_seconds()) < 900
        )
        is_best_discharge_now = (
            ps is not None
            and ps.best_discharge_slot is not None
            and abs((ps.best_discharge_slot.start - now).total_seconds()) < 900
        )

        if (is_cheap or is_best_charge_now) and (is_night or buy_price < 0.30):
            if soc < self.winter_max_soc:
                decision.battery_charge_power_w = state.battery_max_power_kw * 1000
                decision.reason += f" | Laddar batteri (billigt {buy_price:.3f} SEK"
                if is_best_charge_now:
                    decision.reason += ", bästa timmen"
                decision.reason += ")"
        elif (is_expensive or is_best_discharge_now) and soc > self.winter_min_soc:
            decision.battery_discharge_power_w = state.battery_max_power_kw * 1000
            decision.reason += f" | Laddar ur batteri (dyrt {buy_price:.3f} SEK"
            if is_best_discharge_now:
                decision.reason += ", bästa timmen"
            decision.reason += ")"
        elif solar_w > 100 and soc < self.winter_max_soc:
            house_load_w = self._house_load(state)
            surplus = max(0.0, solar_w - house_load_w)
            if surplus > 100:
                decision.battery_charge_power_w = min(state.battery_max_power_kw * 1000, surplus)
                decision.reason += " | Laddar batteri från sol (vinter)"

        # EV: endast solöverskott i vinterläge
        house_load_w = self._house_load(state)
        remaining_surplus = max(0.0, solar_w - house_load_w)
        for i, ch in enumerate(state.chargers):
            if not ch.connected or ch.active_car_name == NO_CAR_SELECTED:
                continue
            min_surplus = MIN_SOLAR_FOR_EV_1PHASE if ch.car_phases == 1 else MIN_SOLAR_FOR_EV_3PHASE
            if remaining_surplus >= min_surplus:
                cur = self._surplus_to_current(remaining_surplus, ch.car_phases)
                if cur >= MIN_EV_CURRENT:
                    decision.charger_decisions[i] = ChargerDecision(
                        enable=True, current_a=cur, reason="solladdar (vinter)",
                    )
                    remaining_surplus -= self._charger_power(cur, ch.car_phases)

        self._check_car_selection(state, decision)
        return self._apply_phase_limits(state, decision)

    # ── Force-lägen ───────────────────────────────────────────────────

    def _force_charge_ev(self, state: EnergyState) -> ControlDecision:
        n = len(state.chargers)
        decision = ControlDecision(
            reason="Force charge EVs from grid",
            charger_decisions=[
                ChargerDecision(
                    enable=ch.connected and ch.active_car_name != NO_CAR_SELECTED,
                    current_a=MAX_EV_CURRENT,
                    reason="forced",
                )
                for ch in state.chargers
            ],
        )
        self._check_car_selection(state, decision)
        return self._apply_phase_limits(state, decision)

    def _force_charge_battery(self, state: EnergyState) -> ControlDecision:
        n = len(state.chargers)
        decision = ControlDecision(
            reason="Force charge battery from grid",
            charger_decisions=[ChargerDecision() for _ in range(n)],
        )
        if state.battery_soc_pct < self.battery_max_soc:
            decision.battery_charge_power_w = state.battery_max_power_kw * 1000
        self._check_car_selection(state, decision)
        return self._apply_phase_limits(state, decision)

    # ── Fasbegränsning ────────────────────────────────────────────────

    def _apply_phase_limits(self, state: EnergyState, decision: ControlDecision) -> ControlDecision:
        solar_per_phase = state.solar_power_w / 3.0
        batt_discharge_per_phase = decision.battery_discharge_power_w / 3.0
        batt_charge_per_phase = decision.battery_charge_power_w / 3.0

        loads: dict[str, float] = {
            "L1": state.grid_power_l1,
            "L2": state.grid_power_l2,
            "L3": state.grid_power_l3,
        }

        for ph in PHASES:
            loads[ph] += -solar_per_phase + batt_charge_per_phase - batt_discharge_per_phase

        # EV-lasterna per laddare
        # Fasbelastningen bestäms av BILENS inbyggda laddare (car_phases),
        # inte laddarhårdvarans fasantal.
        ev_phase_loads: list[dict[str, float]] = []
        for i, (ch, dec) in enumerate(zip(state.chargers, decision.charger_decisions)):
            ph_load: dict[str, float] = {p: 0.0 for p in PHASES}
            if dec.enable and dec.current_a > 0:
                active_phases = ch.effective_phases  # [L1], [L1,L2] eller [L1,L2,L3]
                per_phase_w = dec.current_a * self.voltage
                for ph in active_phases:
                    ph_load[ph] = per_phase_w
            ev_phase_loads.append(ph_load)
            for ph in PHASES:
                loads[ph] += ph_load[ph]

        # Värmepump kompressor
        if state.heat_pump_power_w:
            hp_ph = state.heat_pump_phase
            loads[hp_ph] = loads.get(hp_ph, 0.0) + state.heat_pump_power_w

        # Elpatron
        if decision.extra_hot_water:
            patron_phases = state.heat_pump_patron_phases or DEFAULT_HEAT_PUMP_PATRON_PHASES
            patron_total_w = state.heat_pump_patron_power_kw * 1000
            patron_per_phase_w = patron_total_w / len(patron_phases)
            for ph in patron_phases:
                loads[ph] = loads.get(ph, 0.0) + patron_per_phase_w

        # Reduktionspass (max 4)
        for iteration in range(4):
            any_violation = False
            for ph in PHASES:
                phase_current = loads[ph] / self.voltage
                if phase_current <= self.max_current:
                    continue
                any_violation = True
                over_w = (phase_current - self.max_current) * self.voltage

                _LOGGER.warning(
                    "Pass %d: fas %s överskriden %.1fA (max %.1fA)",
                    iteration, ph, phase_current, self.max_current,
                )

                # Prio 1: EV (lägst prio sist)
                for i in range(len(state.chargers) - 1, -1, -1):
                    if over_w <= 0:
                        break
                    ch = state.chargers[i]
                    dec = decision.charger_decisions[i]
                    if not dec.enable:
                        continue

                    active_phases = ch.effective_phases  # [L1], [L1,L2] eller [L1,L2,L3]

                    # Påverkar denna laddare den överbelastade fasen?
                    if ph not in active_phases:
                        continue

                    max_reduce_a = dec.current_a - MIN_EV_CURRENT
                    if max_reduce_a <= 0:
                        # Stäng av laddaren helt
                        dec.enable = False
                        dec.current_a = 0
                        for p in active_phases:
                            loads[p] -= ev_phase_loads[i][p]
                            ev_phase_loads[i][p] = 0
                    else:
                        # Minska strömmen – påverkar alla bilens aktiva faser lika
                        reduce_a = min(max_reduce_a, over_w / self.voltage)
                        dec.current_a -= reduce_a
                        for p in active_phases:
                            delta = reduce_a * self.voltage
                            loads[p] -= delta
                            ev_phase_loads[i][p] -= delta
                    over_w = max(0, (loads[ph] / self.voltage - self.max_current) * self.voltage)

                # Prio 2: batteriladdning
                if over_w > 0 and decision.battery_charge_power_w > 0:
                    reduce_w_total = min(over_w * 3, decision.battery_charge_power_w)
                    decision.battery_charge_power_w -= reduce_w_total
                    for p in PHASES:
                        loads[p] -= reduce_w_total / 3.0
                    over_w = max(0, (loads[ph] / self.voltage - self.max_current) * self.voltage)

                # Prio 3: elpatron
                if over_w > 0 and decision.extra_hot_water:
                    patron_phases = state.heat_pump_patron_phases or DEFAULT_HEAT_PUMP_PATRON_PHASES
                    if ph in patron_phases:
                        patron_per_phase = state.heat_pump_patron_power_kw * 1000 / len(patron_phases)
                        for pp in patron_phases:
                            loads[pp] -= patron_per_phase
                        decision.extra_hot_water = False
                        _LOGGER.warning("Stänger av elpatron pga fasgräns på %s", ph)

            if not any_violation:
                break

        decision.phase_loads = PhaseLoad(
            L1=loads.get("L1", 0.0),
            L2=loads.get("L2", 0.0),
            L3=loads.get("L3", 0.0),
        )
        return decision

    # ── Hjälpare ──────────────────────────────────────────────────────

    def _house_load(self, state: EnergyState) -> float:
        if state.house_load_w > 0:
            return state.house_load_w
        return max(0.0, (
            state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3
            + state.solar_power_w
            + max(0, -state.battery_power_w)
            - max(0, state.battery_power_w)
        ))


    def _can_start_extra_hot_water(self, state) -> bool:
        """Returnera True om extra varmvatten är tillåtet baserat på temperatur.

        Två gränser:
          min_temp: starta inte om tanken redan är varm (> min_temp)
          max_temp: stäng av om tanken är för het (> max_temp)
        """
        if state.hot_water_temp_c is None:
            return True  # Ingen sensor konfigurerad – tillåt alltid
        temp = state.hot_water_temp_c
        if temp >= state.extra_hot_water_max_temp:
            return False   # För varmt – stäng av
        if temp >= state.extra_hot_water_min_temp:
            return False   # Redan tillräckligt varmt – vänta
        return True

    def _surplus_to_current(self, surplus_w: float, phases: int) -> float:
        """Beräkna laddström från solöverskott baserat på antal faser.

        Returnerar 0 om överskottet inte räcker till minimiströmmen,
        så att anroparens `cur >= MIN_EV_CURRENT`-kontroll är meningsfull.
        """
        current = round(surplus_w / (self.voltage * phases))
        if current < MIN_EV_CURRENT:
            return 0.0
        return float(min(MAX_EV_CURRENT, current))

    def _charger_power(self, current_a: float, phases: int) -> float:
        """Total effekt för en laddare vid given ström och fasantal."""
        return current_a * self.voltage * phases

    def calculate_buy_price(self, spot: float, grid_fees: float, energy_tax: float, vat: float) -> float:
        return (spot + grid_fees + energy_tax) * (1 + vat)

    def calculate_sell_price(self, spot: float, extra_revenue: float) -> float:
        return spot + extra_revenue
