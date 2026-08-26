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
    DEFAULT_CHEAP_CHARGE_MAX_SOLAR_KWH, DEFAULT_CHEAP_CHARGE_BUY_PERCENTILE,
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

    # Snittpris för energin i batteriet (SEK/kWh) – från BatteryAccumulatedCostSensor
    battery_avg_cost_sek_kwh: float = 0.0

    # Opportunistisk nätladdning aktiverad
    opportunistic_charge_enabled: bool = True

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
    # Controllerens beräknade kvällsmål (SOC %) för plan-executor-sensor
    evening_target_soc: float = 0.0

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
        export_min_sell_price_sek_kwh: float = 0.0,
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
        self.export_min_sell_price_sek_kwh = export_min_sell_price_sek_kwh

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

        # Bästa förbrukningsestimering: max av temperaturmodell och gårdagens faktiska.
        # Temperaturmodellen underskattar baslasten kraftigt i sommar (modellerar bara
        # värmepump, inte elektronik/kyl/belysning). yesterday_consumption_kwh inkluderar
        # allt utom EV-laddning → korrekt jämförelseunderlag.
        _eff_daily_kwh = max(
            state.predicted_daily_kwh,
            state.yesterday_consumption_kwh or 0.0,
        )
        if _eff_daily_kwh > 0 and ps and ps.slots:
            # yesterday_consumption_kwh = nätuttag (underskattar total hushållslast på soldagar).
            # Klämma mot faktisk huslast och minimigolv precis som exportgolvets v0.5.27-fix.
            hourly_load_kw = min(max(_eff_daily_kwh / 24.0, house_load_w / 1000.0, 0.5), 1.5)

            # Hitta solar takeover IMORGON BITTI, inte idag.
            # Utan denna fix hittar loopen dagens solproduktion (om 8 min) → hours_dark = 8 min
            # → evening_target_soc ≈ 6% → batteri vid 20% räknas som "klart" → evening_fill = False.
            # Lösning: under dagtid söker vi bara slots som startar EFTER solnedgång.
            _sun_rising = state.sun_next_rising
            if _sun_rising and _sun_rising.tzinfo is None:
                _sun_rising = _sun_rising.astimezone()
            _is_daytime = (
                sun_set is not None
                and _sun_rising is not None
                and sun_set < _sun_rising
            )
            _dark_start = sun_set if _is_daytime else now_aware

            solar_covers_at: Optional[datetime] = None
            for slot in ps.slots:
                if slot.start >= _dark_start and slot.solar_kw >= hourly_load_kw:
                    solar_covers_at = slot.start
                    break

            if solar_covers_at is None and _sun_rising:
                # Solcast saknar data bortom idag – uppskatta 3h efter soluppgång
                solar_covers_at = _sun_rising + timedelta(hours=3)

            if solar_covers_at is not None:
                hours_dark = max(0.0, (solar_covers_at - _dark_start).total_seconds() / 3600)
                evening_needed_kwh = hourly_load_kw * hours_dark + 2.0  # +2 kWh laddmarginal
                # battery_min_soc är oanvändbar energi längst ner – lägg till den
                # annars ger 11 kWh behov bara (11/33)*100=34% som har 4.9 kWh tillgänglig.
                evening_target_soc = min(
                    self.battery_max_soc,
                    self.battery_min_soc + evening_needed_kwh / state.battery_capacity_kwh * 100.0,
                )
                _LOGGER.debug(
                    "Kvällsfylling dynamisk: %.1f kWh behövs (%.1fh mörker, från %s) → mål %.0f%% SOC",
                    evening_needed_kwh, hours_dark,
                    _dark_start.strftime("%H:%M"), evening_target_soc,
                )

        decision.evening_target_soc = evening_target_soc

        battery_remaining_kwh = (
            state.battery_capacity_kwh
            * max(0.0, evening_target_soc - battery_soc)
            / 100.0
        )

        evening_fill = (
            sun_set is not None
            and solar_w > 100
            and battery_soc < evening_target_soc
            and (
                solar_until_sunset_kwh < battery_remaining_kwh
                # Framtida sol exporteras om säljpriset är högt → räkna den inte som
                # garanterad batteriladdning; fyll luckan nu med överskott istället.
                or sell_price >= self.sell_solar_min_price
            )
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

        if remaining_surplus > 100 and battery_soc < self.battery_max_soc and decision.battery_discharge_power_w == 0.0:
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
                decision.reason += f" | Batteri laddar{suffix}"

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
        #   2. Nettosol imorgon ≥ exportgolv (slot-baserat: prognos minus husförbrukning)
        #   3. Batteri > nattens energibehov slot-för-slot (täcker huset tills sol tar över)
        #   4. Batteri > battery_min_soc (absolut golv oavsett)
        _now_utc = datetime.now(timezone.utc)
        _ref_dt = state.solar_takeover_dt or state.sun_next_rising
        _ref_load_w = (state.yesterday_consumption_kwh / 24.0 * 1000.0) if state.yesterday_consumption_kwh else house_load_w
        # Nattlasten underskattas på soldagar (yesterday_kwh = bara nätuttag, inte total hushålls­last).
        # Klämma mot faktisk huslast och lägsta rimliga nattnivå för att golvet täcker natten.
        _hourly_load_kw = min(max(_ref_load_w, house_load_w, 500.0), 1500.0) / 1000.0

        # Slot-baserat exportgolv: Σ max(0, huslast − solar_kw) per slot från nu till solar takeover.
        # Exaktare än timmar×snittlast – morgon/kvällsramper där sol täcker delar av lasten
        # bidrar med reducerat behov, inte fullt 875 W hela natten.
        if ps and ps.slots and _ref_dt and _ref_dt > _now_utc:
            _ref_dt_local = _ref_dt.astimezone()
            _now_local_f = _now_utc.astimezone()
            _floor_slots = [s for s in ps.slots if s.end > _now_local_f and s.start < _ref_dt_local]
            _export_floor_kwh = (
                sum(
                    max(0.0, _hourly_load_kw * (s.end - s.start).total_seconds() / 3600.0 - s.solar_kwh)
                    for s in _floor_slots
                ) + 2.0
            ) if _floor_slots else 2.0
        else:
            _export_floor_kwh = _hourly_load_kw * 9.0 + 2.0

        # Exportprisjämförelse: sälj bara om säljpriset täcker kommande dyra mörka slots.
        # Annars är det bättre att behålla energin för självkonsumtion (sparar mer än exportintäkten).
        # Gräns: sell_price ≥ 90 % av max köppris bland mörka slots i golvperioden.
        _DARK_SOLAR_KW_CTRL = 2.0
        if ps and ps.slots and _ref_dt and _ref_dt > _now_utc:
            _max_night_buy = max(
                (s.buy_sek for s in _floor_slots if s.solar_kw < _DARK_SOLAR_KW_CTRL),
                default=0.0,
            )
        else:
            _max_night_buy = 0.0
        _export_price_ok = _max_night_buy <= 0.0 or sell_price >= _max_night_buy * 0.9

        # Användbar energi ovan min_soc – energin under min_soc kan aldrig nås.
        _battery_energy_kwh = max(0.0, battery_soc - self.battery_min_soc) / 100.0 * state.battery_capacity_kwh

        # Nettosol imorgon slot-för-slot: prognos minus husförbrukning per 15-minutersslot.
        # Förhindrar export när uppdaterad prognos (t.ex. 22 kWh) minus husförbrukning (13 kWh)
        # ger otillräcklig nettosol (9 kWh) för att fylla batteriet igen (golv ~10 kWh).
        # Det råa råproduktionsvillkoret (≥20 kWh) fångade inte detta – 22 ≥ 20 är sant
        # men nettosolen räcker ändå inte.
        if ps and ps.slots:
            _tomorrow_date = (datetime.now().astimezone() + timedelta(days=1)).date()
            _tomorrow_slots = [s for s in ps.slots if s.start.astimezone().date() == _tomorrow_date]
            _net_solar_tomorrow_kwh = (
                sum(
                    max(0.0, s.solar_kwh - _hourly_load_kw * (s.end - s.start).total_seconds() / 3600.0)
                    for s in _tomorrow_slots
                ) if _tomorrow_slots
                else max(0.0, state.solar_forecast_tomorrow_kwh - _hourly_load_kw * 13.0)
            )
        else:
            _net_solar_tomorrow_kwh = max(0.0, state.solar_forecast_tomorrow_kwh - _hourly_load_kw * 13.0)

        _avg_cost = state.battery_avg_cost_sek_kwh

        export_active = False
        if (
            ps and ps.slots
            and battery_soc > self.battery_min_soc
            and _battery_energy_kwh > _export_floor_kwh
            and _net_solar_tomorrow_kwh >= _export_floor_kwh
            and (_avg_cost <= 0.0 or sell_price > _avg_cost)
            and solar_w <= house_load_w + 200
            and _export_price_ok
        ):
            # Använd bara dagens slots för percentilberäkningen så att imorgons
            # priser inte höjer tröskeln när priserna är generellt höga.
            _today_date = datetime.now().astimezone().date()
            _today_slots = [s for s in ps.slots if s.start.astimezone().date() == _today_date]
            today_sell_prices = sorted(s.sell_sek for s in (_today_slots or ps.slots))
            if today_sell_prices:
                idx = int(self.export_sell_percentile * len(today_sell_prices))
                idx = min(idx, len(today_sell_prices) - 1)
                price_threshold = today_sell_prices[idx]
                _abs_triggered = (
                    self.export_min_sell_price_sek_kwh > 0
                    and sell_price >= self.export_min_sell_price_sek_kwh
                )
                if sell_price >= price_threshold or _abs_triggered:
                    export_active = True
                    _trigger_label = (
                        f"≥absolut {self.export_min_sell_price_sek_kwh:.2f}"
                        if _abs_triggered and sell_price < price_threshold
                        else f"≥{self.export_sell_percentile*100:.0f}:e percentil {price_threshold:.2f}"
                    )

                    # Modulera effekten med prisväktad dispatch över HELA den lönsamma
                    # fönstret – kväll + morgon imorgon om priset är högre då.
                    # Avgränsning via solproduktion (< 2 kW) snarare än klockslag så att
                    # höga morgontimmar (07–09) inkluderas medan middagen exkluderas.
                    # Utan detta exporteras allt kväll och inget finns kvar till morgonens topp.
                    _now_local = datetime.now().astimezone()
                    # Dispatch-fönstret använder alltid percentiltröskeln, oavsett om
                    # abs-minimum triggade exporten. Annars skapas ett natt-långt fönster
                    # med alla billiga slots (≥0.70 kr) och batteriet töms till min-SOC.
                    _effective_threshold = price_threshold
                    _solar_resume = state.solar_takeover_dt or state.sun_next_rising
                    _solar_resume_local = _solar_resume.astimezone() if _solar_resume else None
                    _has_solar_data = any(s.solar_kw > 0 for s in ps.slots)
                    _window_end = _now_local + timedelta(hours=20)
                    _COMPETING_SOLAR_KW = 2.0
                    _high_slots = [
                        s for s in ps.slots
                        if s.sell_sek >= _effective_threshold
                        and s.end > _now_local
                        and s.start < _window_end
                        and (
                            # Med Solcast: exkludera slots där sol verkligen producerar
                            # (> 2 kW) – natt och tidig morgon inkluderas automatiskt
                            s.solar_kw < _COMPETING_SOLAR_KW if _has_solar_data
                            # Utan Solcast: klipp vid sol-resume (gammal logik)
                            else (_solar_resume_local is None or s.start < _solar_resume_local)
                        )
                    ]
                    _high_hours = sum(
                        (s.end - max(s.start, _now_local)).total_seconds() / 3600.0
                        for s in _high_slots
                    )
                    _exportable_kwh = _battery_energy_kwh - _export_floor_kwh
                    _remaining_slots = [
                        s for s in _high_slots
                        if s.end > _now_local
                    ]

                    # Om abs-minimum triggade exporten men inga höga prisslots återstår
                    # → stäng av export. Abs-minimum är override för enstaka svaga slots,
                    # inte licens att exportera hela natten på låga priser.
                    if not _remaining_slots and _abs_triggered and sell_price < price_threshold:
                        export_active = False
                        decision.reason += " | Proaktiv export stoppad: inga höga prisslots kvar"
                        _LOGGER.debug(
                            "Proaktiv export stoppad kl %s: abs-trigger men inga slots ≥ %.2f kr kvar",
                            datetime.now().astimezone().strftime("%H:%M"),
                            price_threshold,
                        )

                    if export_active:
                        _price_sum = sum(s.sell_sek for s in _remaining_slots)
                        if _price_sum > 0:
                            _weight = sell_price / _price_sum
                            _slot_hours = 0.25
                            _target_w = (_weight * _exportable_kwh / _slot_hours) * 1000.0
                        else:
                            _safe_hours = max(1.0, _high_hours)
                            _target_w = (_exportable_kwh / _safe_hours) * 1000.0
                        # Sonnen discharge_setpoint = totalt batteriutflöde (hus + nät).
                        # Lägg till husunderskott så att batteriet täcker huset och exporterar
                        # _target_w netto till nätet – annars kompenserar nätet huslasten.
                        _house_deficit_w = max(0.0, house_load_w - solar_w)
                        discharge_w = max(500.0, min(
                            _target_w + _house_deficit_w,
                            state.battery_max_power_kw * 1000.0,
                        ))
                        _net_export_w = max(0.0, discharge_w - _house_deficit_w)

                        decision.battery_discharge_power_w = discharge_w
                        _morning_slots = [s for s in _high_slots if s.start.astimezone().date() > _today_date]
                        decision.reason += (
                            f" | Proaktiv export {sell_price:.2f} kr/kWh"
                            f" ({_trigger_label})"
                            f" sol imorgon {state.solar_forecast_tomorrow_kwh:.1f} kWh"
                            f" golv {_export_floor_kwh:.1f} kWh"
                            f" {_net_export_w:.0f}W netto ({discharge_w:.0f}W tot) vikt {sell_price:.2f}/{_price_sum:.2f}"
                            + (f" +{len(_morning_slots)} morgonslots" if _morning_slots else "")
                        )
                        _LOGGER.info(
                            "Proaktiv export: %.0f W netto (%.0f W tot, hus %.0f W) säljpris %.3f kr/kWh (%s)"
                            " | batteri %.1f kWh > golv %.1f kWh | prisvikt %.3f/%.3f"
                            " | fönster: %d slots (%d imorgon)",
                            _net_export_w, discharge_w, _house_deficit_w,
                            sell_price, _trigger_label,
                            _battery_energy_kwh, _export_floor_kwh, sell_price, _price_sum,
                            len(_high_slots), len(_morning_slots),
                        )

        # Morgonexport: sälj för att ge plats åt kommande solöverskott.
        # Triggar när:
        #   1. Förväntat solöverskott > kvarvarande batterikapacitet (verkligt behov av plats)
        #   2. Nuvarande säljpris > snitt säljpris under kvarvarande solperiod (lönar sig att sälja nu)
        #   3. Batteri > golv (finns energi att sälja utan att riskera nattens behov)
        _now_loc = datetime.now().astimezone()
        _ref_daily_kwh = state.yesterday_consumption_kwh or 25.0
        _battery_remaining_kwh = (
            state.battery_capacity_kwh * self.battery_max_soc / 100.0 - _battery_energy_kwh
        )
        _expected_solar_surplus = (state.solar_forecast_today_kwh or 0.0) - _ref_daily_kwh
        _need_room = _expected_solar_surplus > _battery_remaining_kwh

        _sun_set = state.sun_next_setting
        _solar_window_slots = (
            [s for s in ps.slots if _now_loc < s.end and (
                _sun_set is None or s.start < _sun_set.astimezone()
            )]
            if ps and ps.slots else []
        )
        # Produktionsviktat snitt: timmar med hög solproduktion väger tyngre än
        # låglastiga morgon/kväll-timmar som drar ner snittet mot dagstimmarnas låga pris.
        _solar_production_total = sum(s.solar_kwh for s in _solar_window_slots)
        if _solar_production_total > 0.0:
            _solar_avg_sell = (
                sum(s.sell_sek * s.solar_kwh for s in _solar_window_slots)
                / _solar_production_total
            )
        elif _solar_window_slots:
            _solar_avg_sell = sum(s.sell_sek for s in _solar_window_slots) / len(_solar_window_slots)
        else:
            _solar_avg_sell = sell_price
        _price_diff_ok = sell_price > _solar_avg_sell

        if not export_active and _need_room and _price_diff_ok and _export_price_ok and (
            _battery_energy_kwh > _export_floor_kwh
            and _avg_cost > 0
            and sell_price > _avg_cost
            and solar_w <= house_load_w + 200
        ):
            export_active = True
            _solar_surplus_w = max(0.0, solar_w - house_load_w)
            discharge_w = max(500.0, state.battery_max_power_kw * 1000.0 - _solar_surplus_w)
            decision.battery_discharge_power_w = discharge_w
            decision.reason += (
                f" | Morgonexport {sell_price:.2f} > snitt {_avg_cost:.2f} kr/kWh"
                f" (solsnitt {_solar_avg_sell:.2f}) overskott {_expected_solar_surplus:.0f} kWh"
            )

        # Opportunistisk nätladdning: ladda billigt när sol imorgon är låg
        if (
            state.opportunistic_charge_enabled
            and not export_active
            and decision.battery_charge_power_w == 0.0
            and ps and ps.slots
            and battery_soc < self.battery_max_soc
        ):
            _ref_daily_kwh = state.yesterday_consumption_kwh or 25.0
            _max_storable_kwh = state.battery_capacity_kwh * self.battery_max_soc / 100.0
            _charge_target_kwh = min(_export_floor_kwh, _max_storable_kwh)
            if state.solar_forecast_tomorrow_kwh < DEFAULT_CHEAP_CHARGE_MAX_SOLAR_KWH:
                _tomorrow_deficit = max(0.0, _ref_daily_kwh - state.solar_forecast_tomorrow_kwh)
                _charge_target_kwh = min(
                    state.battery_capacity_kwh * self.battery_max_soc / 100.0,
                    _export_floor_kwh + _tomorrow_deficit,
                )
            _charge_deficit_kwh = _charge_target_kwh - _battery_energy_kwh
            if _charge_deficit_kwh > 0.5:
                _now_loc = datetime.now().astimezone()
                _future_slots = [s for s in ps.slots if s.end > _now_loc]
                if _future_slots:
                    _sorted_prices = sorted(s.buy_sek for s in _future_slots)
                    _threshold_idx = max(0, int(len(_sorted_prices) * DEFAULT_CHEAP_CHARGE_BUY_PERCENTILE) - 1)
                    _price_threshold = _sorted_prices[_threshold_idx]
                    _cur_slot = next((s for s in _future_slots if s.start <= _now_loc), None)
                    if _cur_slot and buy_price <= _price_threshold:
                        _cheap_slots = [s for s in _future_slots if s.buy_sek <= _price_threshold]
                        _remaining_cheap_h = sum(
                            (s.end - max(s.start, _now_loc)).total_seconds() / 3600.0
                            for s in _cheap_slots
                        )
                        _safe_h = max(1.0, _remaining_cheap_h)
                        charge_w = min(
                            state.battery_max_power_kw * 1000.0,
                            (_charge_deficit_kwh / _safe_h) * 1000.0,
                        )
                        decision.battery_charge_power_w = charge_w
                        decision.reason += (
                            f" | Opp. laddning {buy_price:.2f} kr/kWh"
                            f" (≤{_price_threshold:.2f}) mål {_charge_target_kwh:.1f} kWh"
                            f" sol imorgon {state.solar_forecast_tomorrow_kwh:.1f} kWh"
                        )

        # Självkonsumtion: täck huslast med batteri när sol inte räcker.
        # Lagrat sol-el är alltid bättre än nätimport oavsett aktuellt pris.
        # Två sätt att sänka evening_target-tröskeln:
        #  1. Morgon/sol: sol väntas inom 2h fyller tillbaka reserven → sänk med 80% av solprognosen.
        #  2. Ekonomisk topp: köppriset nu ≥2× billigaste nattladdning → lad ur till battery_min_soc,
        #     billigare att köpa tillbaka billig natelektricitet än att importera dyrt nu.
        _effective_evening_target = evening_target_soc
        if ps is not None and wait_solar and ps.solar_next_2h_kwh > 0 and state.battery_capacity_kwh > 0:
            _solar_soc_gain = 0.8 * ps.solar_next_2h_kwh / state.battery_capacity_kwh * 100.0
            _effective_evening_target = max(self.battery_min_soc, evening_target_soc - _solar_soc_gain)
        _cheap_refill_price = ps.best_charge_slot.buy_sek if (ps and ps.best_charge_slot) else buy_price
        _economic_peak = _cheap_refill_price > 0 and buy_price >= _cheap_refill_price * 1.5
        if _economic_peak:
            _effective_evening_target = self.battery_min_soc
        if not export_active and decision.battery_charge_power_w == 0.0 and solar_w < house_load_w and battery_soc > self.battery_min_soc and battery_soc > _effective_evening_target:
            deficit_w = house_load_w - solar_w
            discharge_w = min(state.battery_max_power_kw * 1000, deficit_w)
            now = datetime.now().astimezone()
            if ps and ps.best_discharge_slot:
                is_peak_now = abs((ps.best_discharge_slot.start - now).total_seconds()) < 900
                if is_peak_now:
                    decision.reason += " | Bästa urladdningstimmen"
            decision.battery_discharge_power_w = discharge_w
            if _economic_peak:
                decision.reason += f" | Självkonsumtion {deficit_w:.0f}W (ekonomisk topp {buy_price:.2f}>{_cheap_refill_price:.2f}×1.5)"
            else:
                solar_note = f" (sol {ps.solar_next_2h_kwh:.1f}kWh/2h)" if wait_solar and ps is not None else ""
                decision.reason += f" | Självkonsumtion {deficit_w:.0f}W{solar_note}"

        if battery_soc <= self.battery_min_soc:
            decision.battery_discharge_power_w = 0
            decision.reason += " | Batteri vid min SOC"

        if decision.battery_charge_power_w > 0 and decision.battery_discharge_power_w > 0:
            _LOGGER.warning(
                "Konflikt: charge=%.0fW och discharge=%.0fW satta samtidigt – ignorerar discharge. Orsak: %s",
                decision.battery_charge_power_w, decision.battery_discharge_power_w, decision.reason,
            )
            decision.battery_discharge_power_w = 0.0

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
        # Elmätarens avläsningar innehåller redan alla nuvarande laster:
        # sol, batteri, EV, värmepump, elpatron.
        # Vi applicerar bara delta för det som beslutet ändrar.
        loads: dict[str, float] = {
            "L1": state.grid_power_l1,
            "L2": state.grid_power_l2,
            "L3": state.grid_power_l3,
        }

        # Batteri: ta bort nuvarande effekt, lägg till beslutets effekt (per fas)
        current_batt_per_phase = state.battery_power_w / 3.0  # positiv=laddning, negativ=urladdning
        new_batt_per_phase = (
            decision.battery_charge_power_w - decision.battery_discharge_power_w
        ) / 3.0
        for ph in PHASES:
            loads[ph] += new_batt_per_phase - current_batt_per_phase

        # EV: ta bort nuvarande laddning (ingår i elmätarvärdet), lägg till beslutets laddning.
        # Fasbelastningen bestäms av BILENS inbyggda laddare (car_phases),
        # inte laddarhårdvarans fasantal.
        ev_phase_loads: list[dict[str, float]] = []
        for i, (ch, dec) in enumerate(zip(state.chargers, decision.charger_decisions)):
            ph_load_new: dict[str, float] = {p: 0.0 for p in PHASES}
            if dec.enable and dec.current_a > 0:
                active_phases = ch.effective_phases  # [L1], [L1,L2] eller [L1,L2,L3]
                per_phase_w = dec.current_a * self.voltage
                for ph in active_phases:
                    ph_load_new[ph] = per_phase_w

            # Dra bort nuvarande EV-effekt (ingår redan i elmätarvärdet)
            if ch.power_w > 0:
                active_phases = ch.effective_phases
                current_ev_per_phase = ch.power_w / len(active_phases)
                for ph in active_phases:
                    loads[ph] -= current_ev_per_phase

            # Lägg till beslutets EV-effekt
            for ph in PHASES:
                loads[ph] += ph_load_new[ph]

            ev_phase_loads.append(ph_load_new)

        # Elpatron: applicera delta om beslutet ändrar tillståndet.
        # Värmepumps-kompressorn ingår redan i elmätarvärdet och styrs inte av SEM.
        patron_phases = state.heat_pump_patron_phases or DEFAULT_HEAT_PUMP_PATRON_PHASES
        patron_total_w = state.heat_pump_patron_power_kw * 1000
        patron_per_phase_w = patron_total_w / len(patron_phases)
        if decision.extra_hot_water and not state.extra_hot_water_on:
            for ph in patron_phases:
                loads[ph] += patron_per_phase_w
        elif not decision.extra_hot_water and state.extra_hot_water_on:
            for ph in patron_phases:
                loads[ph] -= patron_per_phase_w

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

                # Prio 3: elpatron (om den är på – oavsett om den slogs på nu eller var på redan)
                if over_w > 0 and decision.extra_hot_water:
                    if ph in patron_phases:
                        for pp in patron_phases:
                            loads[pp] -= patron_per_phase_w
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
