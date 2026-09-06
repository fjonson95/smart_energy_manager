"""Core energy control logic for Smart Energy Manager."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
from datetime import datetime, timedelta, timezone

from .price_scheduler import PriceSchedule

if TYPE_CHECKING:
    from .energy_planner import DayPlan
from .const import (
    PHASES, DEFAULT_MAX_CURRENT, DEFAULT_GRID_VOLTAGE,
    MIN_EV_CURRENT, MAX_EV_CURRENT,
    MIN_SOLAR_FOR_EV_1PHASE, MIN_SOLAR_FOR_EV_3PHASE,
    NEGATIVE_PRICE_THRESHOLD,
    EV_PHASE_L1, NO_CAR_SELECTED,
    MODE_AUTO, MODE_FORCE_CHARGE_EV,
    MODE_FORCE_CHARGE_BATTERY, MODE_MANUAL,
    DEFAULT_HEAT_PUMP_PHASE, DEFAULT_HEAT_PUMP_PATRON_PHASES,
    DEFAULT_HEAT_PUMP_PATRON_POWER_KW,
    DEFAULT_CHEAP_CHARGE_MAX_SOLAR_KWH, DEFAULT_CHEAP_CHARGE_BUY_PERCENTILE,
    DEFAULT_MAX_EXPORT_W, DEFAULT_PHASE_CURRENT_MARGIN,
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
    # Namn på vald bil (NO_CAR_SELECTED = ingen vald ännu)
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

    # Glidande medel (några minuter, satt av coordinatorn) av house_load_w –
    # bara för kvällsmålets timmar-framåt-projektion i _auto_mode(), som annars
    # tar en enstaka kokplatta/dusch och antar att den pågår hela natten.
    # None (t.ex. i backtest) faller tillbaka på house_load_w.
    house_load_avg_w: Optional[float] = None

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

    # Aktuell planslot-action från EnergyPlanner ("export", "grid_charge", "cover_load", "idle", …)
    # Sätts av coordinator precis innan compute() anropas. None = ingen plan tillgänglig.
    plan_action: Optional[str] = None

    # DayPlan.export_floor_kwh – batterienergi som ska sparas till att solen tar över.
    # Sätts av coordinator precis innan compute() anropas. None = ingen plan tillgänglig.
    plan_export_floor_kwh: Optional[float] = None

    # Driftläge
    operating_mode: str = MODE_AUTO

    # "Nu", satt av coordinator via homeassistant.util.dt.now() (HA:s konfigurerade
    # tidszon, inte containerns systemtid). None = fallback till datetime.now()
    # nedan – håller den här filen fri från HA-beroenden för standalone/backtest-bruk.
    now: Optional[datetime] = None


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
        max_export_w: float = DEFAULT_MAX_EXPORT_W,
        battery_min_soc: float = 10.0,
        battery_max_soc: float = 95.0,
        auto_discharge_threshold_sek: float = 0.20,
        sell_solar_min_price_sek: float = 0.80,
        evening_min_soc: float = 90.0,
    ):
        self.max_current = max_current_per_phase
        self.max_current_effective = max(0.0, max_current_per_phase - DEFAULT_PHASE_CURRENT_MARGIN)
        self.voltage = grid_voltage
        self.max_phase_power = max_current_per_phase * grid_voltage
        self.max_export_w = max_export_w
        self.battery_min_soc = battery_min_soc
        self.battery_max_soc = battery_max_soc
        self.auto_discharge_threshold = auto_discharge_threshold_sek
        self.sell_solar_min_price = sell_solar_min_price_sek
        self.evening_min_soc = evening_min_soc

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

        # Korrigera surplus via energibalans när nätet faktiskt exporterar:
        #   riktigt_överskott = nätexport + pågående_batteriladdning
        # Huslastsensorn kan överskatta (inkluderar laster direktförsörjda av sol-L3)
        # vilket gör solar−house för litet. Formeln gäller BARA vid aktiv export —
        # används den även vid nätimport skapas en feedback-loop som håller kvar
        # batteriladdningen trots att solöverskottet är borta.
        _grid_total_w = state.grid_power_l1 + state.grid_power_l2 + state.grid_power_l3
        _batt_charge_w = max(0.0, state.battery_power_w)
        _raw_export_w = max(0.0, -_grid_total_w)
        if _raw_export_w > 0:
            _effective_surplus_w = _raw_export_w + _batt_charge_w
            if _effective_surplus_w > solar_surplus_w:
                _LOGGER.debug(
                    "Nät-export surplus korrigering: %.0fW (export=%.0fW batt_charge=%.0fW) > beräknat %.0fW",
                    _effective_surplus_w, _raw_export_w, _batt_charge_w, solar_surplus_w,
                )
                solar_surplus_w = _effective_surplus_w

        _LOGGER.debug(
            "Auto: solar=%.0fW house=%.0fW surplus=%.0fW soc=%.0f%% buy=%.3f neg=%s",
            solar_w, house_load_w, solar_surplus_w, battery_soc, buy_price, negative_price,
        )

        # ── Proaktiv absorption: negativa priser väntar inom 2h ─────
        # Håller headroom i batteriet genom att sänka laddningstaket – men
        # fortsätter genom den vanliga logiken (självkonsumtion, EV,
        # varmvatten) istället för att stänga av allt i väntan på att priset
        # faktiskt blir negativt.
        ps = state.price_schedule
        had_negative_today = ps is not None and ps.negative_slots_passed_today > 0
        _charge_max_soc = self.battery_max_soc
        if ps and ps.should_absorb_proactively and not negative_price:
            decision.reason += f" | Headroom inför neg pris ({ps.negative_slots_ahead} slots inom 8h)"
            effective_max_soc = self.battery_max_soc - (ps.recommended_headroom * 100)
            if battery_soc > effective_max_soc:
                _charge_max_soc = effective_max_soc
                decision.reason += f" | Håller {ps.recommended_headroom*100:.0f}% headroom"

        # ── Negativt säljpris just nu: absorptionstrappan ──────────────
        # Steg (i ordning, nästa steg bara om föregående är mättat):
        #   1. Batteri – full effekt (nät ELLER sol, oavsett – man får betalt för att äta)
        #   2. Varmvatten
        #   3. Värme (etapp 5 – ej implementerat än)
        #   4. Bil
        #   5. Strypning av växelriktaren (kräver P4-1:s manuella kalibrering – ej implementerat)
        # Villkoret krävde tidigare solar_w > 0, vilket gjorde att ett negativt
        # nattpris utan sol aldrig utnyttjades alls.
        if negative_price:
            if battery_soc < self.battery_max_soc:
                decision.battery_charge_power_w = state.battery_max_power_kw * 1000
                decision.reason += " | Negativt pris steg 1: batteri full effekt"
            elif self._can_start_extra_hot_water(state):
                decision.extra_hot_water = True
                decision.reason += " | Negativt pris steg 2: varmvatten"
            else:
                _absorbed_ev = False
                for i, ch in enumerate(state.chargers):
                    if not ch.connected or ch.active_car_name == NO_CAR_SELECTED:
                        continue
                    car = ch.active_car
                    if car and ch.soc_pct is not None and ch.soc_pct >= car.ev_soc_target:
                        continue
                    decision.charger_decisions[i] = ChargerDecision(
                        enable=True, current_a=MAX_EV_CURRENT,
                        reason="negativt pris – laddar bilen",
                    )
                    _absorbed_ev = True
                if _absorbed_ev:
                    decision.reason += " | Negativt pris steg 4: bil"
                else:
                    decision.reason += " | Negativt pris – inget kvar att absorbera i (strypning ej implementerad)"

            self._check_car_selection(state, decision)
            return self._apply_phase_limits(state, decision)

        # ── Normal auto ───────────────────────────────────────────────
        remaining_surplus = solar_surplus_w

        # Husbatteri prioriteras före EV (CLAUDE.md prioritet #2: maximera egenförbrukning).
        # Reservera batteriets andel ur remaining_surplus INNAN EV-loopen – men bara om
        # Sonnen faktiskt absorberar (> 100 W). När batteriet är nära fullt throttlar Sonnen
        # och tar inte emot effekten; då pre-emptas ingenting och EV får överskottet.
        _pre_buy_ref = ps.best_charge_slot.buy_sek if (ps and ps.best_charge_slot) else None
        _pre_prefer_sell = (
            sell_price >= self.sell_solar_min_price
            and (_pre_buy_ref is None or sell_price >= _pre_buy_ref * 0.90)
        )
        _actual_batt_charge_w = max(0.0, state.battery_power_w)
        _battery_preempt_w = 0.0
        if (
            battery_soc < _charge_max_soc
            and not _pre_prefer_sell
            and decision.battery_discharge_power_w == 0.0
            and _actual_batt_charge_w > 100
        ):
            _battery_preempt_w = min(state.battery_max_power_kw * 1000, remaining_surplus)
            remaining_surplus = max(0.0, remaining_surplus - _battery_preempt_w)

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

        # Återlägg reserverad batterikapacitet – EV-loopen körde på reducerat överskott;
        # det reserverade beloppet plus ev. EV-rest utgör nu vad batteriet kan ta.
        remaining_surplus += _battery_preempt_w

        # Batteriladdning från solöverskott – tre styrfaktorer:
        #
        # 1. Kvällsfylling: efter evening_fill_hour, fyll alltid batteriet
        #    inför natten (solar_w > 0 = solen fortfarande uppe).
        # 2. Säljpris: om säljpris är högt, exportera hellre än att lagra.
        # 3. Vänta på sol: om solen knappt producerar men stor sol väntas,
        #    håll plats i batteriet.
        ps = state.price_schedule
        now_aware = state.now or datetime.now().astimezone()

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
            # Använder house_load_avg_w (glidande medel), inte den momentana house_load_w:
            # den senare multipliceras med hela mörkerperioden (timmar) nedan, så en enstaka
            # kokplatta/dusch på några minuter annars läses som "detta är natten igenom" och
            # skjuter kvällsmålet över batteriets SOC, vilket stänger av egenförbrukningen
            # helt tills toppen klingar av (upptäckt via ett verkligt fall 2026-09-06).
            _load_for_projection_w = (
                state.house_load_avg_w if state.house_load_avg_w is not None else house_load_w
            )
            hourly_load_kw = max(_eff_daily_kwh / 24.0, _load_for_projection_w / 1000.0, 0.5)

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
        # Jämför mot billigaste framtida köpslot: om vi kan sälja för 0.86 kr men köpa
        # tillbaka för 1.74 kr är det en förlustaffär – lagra i batteriet istället.
        # prefer_sell aktiveras bara om säljpriset är ≥90 % av bästa framtida köppris.
        _prefer_sell_buy_ref = (
            ps.best_charge_slot.buy_sek if (ps and ps.best_charge_slot) else None
        )
        prefer_sell = (
            sell_price >= self.sell_solar_min_price
            and (_prefer_sell_buy_ref is None or sell_price >= _prefer_sell_buy_ref * 0.90)
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

        if remaining_surplus > 100 and battery_soc < _charge_max_soc and decision.battery_discharge_power_w == 0.0:
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

        # Export styrs av EnergyPlanner via coordinator – plan_action sätts i EnergyState
        # precis innan compute() anropas. Kontrollern respekterar beslutet som guard-flagga
        # (blockerar självkonsumtion och opportunistisk laddning under export).
        export_active = state.plan_action == "export"

        # Economic peak beräknas tidigt – används både här och i självkonsumtion-blocket.
        _cheap_refill_price = ps.best_charge_slot.buy_sek if (ps and ps.best_charge_slot) else buy_price
        _economic_peak = _cheap_refill_price > 0 and buy_price >= _cheap_refill_price * 1.2

        # Opportunistisk nätladdning: ladda billigt när sol idag eller imorgon är låg.
        # Körs ej under economic peak – då ska batteriet laddas ur (dyrt nu, billigare sen).
        # Körs även om solöverskottsladdning redan är aktiv – adderar nätladdning om den
        # når högre effekt än vad solöverskottet ensamt ger (solöverskott har alltid prioritet).
        _low_solar_today = (
            state.solar_forecast_today_kwh > 0
            and state.solar_forecast_today_kwh < DEFAULT_CHEAP_CHARGE_MAX_SOLAR_KWH
        )
        _low_solar_tomorrow = state.solar_forecast_tomorrow_kwh < DEFAULT_CHEAP_CHARGE_MAX_SOLAR_KWH
        if (
            state.opportunistic_charge_enabled
            and not export_active
            and not _economic_peak
            and (_low_solar_today or _low_solar_tomorrow)
            and ps and ps.slots
            and battery_soc < _charge_max_soc
        ):
            _battery_energy_kwh = battery_soc / 100.0 * state.battery_capacity_kwh
            _export_floor_kwh = (
                state.plan_export_floor_kwh if state.plan_export_floor_kwh is not None
                else self.battery_min_soc / 100.0 * state.battery_capacity_kwh
            )
            _ref_daily_kwh = state.yesterday_consumption_kwh or 25.0
            _max_storable_kwh = state.battery_capacity_kwh * _charge_max_soc / 100.0
            # Välj lägsta sol-prognos som referens – mulen idag OCH imorgon → störst underskott
            _solar_ref_kwh = min(
                state.solar_forecast_today_kwh if _low_solar_today else state.solar_forecast_tomorrow_kwh,
                state.solar_forecast_tomorrow_kwh if _low_solar_tomorrow else state.solar_forecast_today_kwh,
            )
            _deficit = max(0.0, _ref_daily_kwh - _solar_ref_kwh)
            _charge_target_kwh = min(_max_storable_kwh, _export_floor_kwh + _deficit)
            _charge_deficit_kwh = _charge_target_kwh - _battery_energy_kwh
            if _charge_deficit_kwh > 0.5:
                _now_loc = state.now or datetime.now().astimezone()
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
                        # Solöverskott har alltid prioritet – addera nätladdning bara om den
                        # kräver högre effekt än vad solen ensamt levererar
                        if charge_w > decision.battery_charge_power_w:
                            _trigger = (
                                f"idag {state.solar_forecast_today_kwh:.1f} kWh"
                                if _low_solar_today
                                else f"imorgon {state.solar_forecast_tomorrow_kwh:.1f} kWh"
                            )
                            decision.battery_charge_power_w = charge_w
                            decision.reason += (
                                f" | Opp. laddning {buy_price:.2f} kr/kWh"
                                f" (≤{_price_threshold:.2f}) mål {_charge_target_kwh:.1f} kWh"
                                f" sol {_trigger}"
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
        if _economic_peak:
            _effective_evening_target = self.battery_min_soc
        if not export_active and decision.battery_charge_power_w == 0.0 and solar_w < house_load_w and battery_soc > self.battery_min_soc and battery_soc > _effective_evening_target:
            deficit_w = house_load_w - solar_w
            discharge_w = min(state.battery_max_power_kw * 1000, deficit_w)
            now = state.now or datetime.now().astimezone()
            if ps and ps.best_discharge_slot:
                is_peak_now = abs((ps.best_discharge_slot.start - now).total_seconds()) < 900
                if is_peak_now:
                    decision.reason += " | Bästa urladdningstimmen"
            decision.battery_discharge_power_w = discharge_w
            if _economic_peak:
                decision.reason += f" | Självkonsumtion {deficit_w:.0f}W (ekonomisk topp {buy_price:.2f}>{_cheap_refill_price:.2f}×1.2)"
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
        # Tvångsladdning av bilen ska inte stoppa batteriet från att fånga
        # kvarvarande solöverskott – annars går det till export istället
        # för att lagras (upptäckt live: hela överskottet gick till nätet
        # medan bilen tvångsladdades).
        house_load_w = self._house_load(state)
        ev_forced_w = sum(
            self._charger_power(dec.current_a, ch.car_phases)
            for ch, dec in zip(state.chargers, decision.charger_decisions)
            if dec.enable
        )
        remaining_surplus = max(0.0, state.solar_power_w - house_load_w - ev_forced_w)
        if remaining_surplus > 100 and state.battery_soc_pct < self.battery_max_soc:
            decision.battery_charge_power_w = min(state.battery_max_power_kw * 1000, remaining_surplus)
            decision.reason += f" | Batteri fångar resterande sol {decision.battery_charge_power_w:.0f}W"
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

        # Tvångsladdning av batteriet ska inte stoppa bilen från att ladda på
        # kvarvarande solöverskott (spegelbild av samma bugg i _force_charge_ev).
        house_load_w = self._house_load(state)
        remaining_surplus = max(0.0, state.solar_power_w - house_load_w - decision.battery_charge_power_w)
        for i, ch in enumerate(state.chargers):
            if not ch.connected or ch.active_car_name == NO_CAR_SELECTED:
                continue
            min_surplus = MIN_SOLAR_FOR_EV_1PHASE if ch.car_phases == 1 else MIN_SOLAR_FOR_EV_3PHASE
            if remaining_surplus >= min_surplus:
                cur = self._surplus_to_current(remaining_surplus, ch.car_phases)
                if cur >= MIN_EV_CURRENT:
                    decision.charger_decisions[i] = ChargerDecision(
                        enable=True, current_a=cur, reason="solladdar under batteriets tvångsladdning",
                    )
                    remaining_surplus -= self._charger_power(cur, ch.car_phases)

        self._check_car_selection(state, decision)
        return self._apply_phase_limits(state, decision)

    # ── Plan-executor (P2-5) ────────────────────────────────────────────

    def apply_plan_executor(
        self,
        day_plan: Optional["DayPlan"],
        operating_mode: str,
        state: EnergyState,
        decision: ControlDecision,
        now: datetime,
        solar_surplus_w: float,
    ) -> ControlDecision:
        """Enda skrivställe för batteriets börvärden när en dagplan finns.

        Planeraren producerar mål per slot; detta klämmer dem mot realtid
        (faktiskt solöverskott, husunderskott, ekonomisk topp, exportgolv)
        och kör om fasskyddet eftersom börvärdena precis ändrats. compute()s
        egen självkonsumtionslogik körs fortfarande (för att räkna fram
        remaining_surplus till EV/varmvatten-sekvenseringen, och som fallback
        utan plan) men dess battery-värden skrivs alltid över här.

        Delad mellan coordinator.py (live drift) och backtest-simulatorn
        (testdata/backtest.py) – enda platsen som avgör det faktiska
        börvärdet, så att en backtest faktiskt speglar produktionsbeteendet.
        """
        if not day_plan or operating_mode != MODE_AUTO:
            return decision

        now_slot = day_plan.slot_at(now)
        if not now_slot:
            return decision

        ps = state.price_schedule
        buy_price = state.buy_price_sek_kwh
        sell_price = state.sell_price_sek_kwh
        house_load_w = state.house_load_w
        solar_w = state.solar_power_w
        ev_total_w = sum(ch.power_w for ch in state.chargers)

        bcp = ps.best_charge_slot.buy_sek if (ps and ps.best_charge_slot) else buy_price
        econ_peak = bcp > 0 and buy_price >= bcp * 1.2
        batt_max_w = state.battery_max_power_kw * 1000.0
        house_def_w = max(0.0, house_load_w - solar_w)
        evening_target = (
            decision.evening_target_soc if decision.evening_target_soc > 0
            else day_plan.evening_target_soc_pct
        )
        self_consume_ok = (
            state.battery_soc_pct > self.battery_min_soc
            and state.battery_soc_pct > evening_target
        )

        # Spara compute()s ursprungliga reason (EV/varmvatten/faskydd m.m.)
        # innan vi bygger om batteridelen – annars försvinner t.ex. vilken
        # laddare som fick sol-ström ur den synliga beslutstexten.
        ev_reason = decision.reason

        decision.battery_charge_power_w = 0.0
        decision.battery_discharge_power_w = 0.0

        if now_slot.action == "solar_charge":
            battery_surplus_w = max(0.0, solar_surplus_w - ev_total_w)
            evening_fill = state.battery_soc_pct < evening_target
            prefer_sell = sell_price >= self.sell_solar_min_price and not evening_fill
            if not prefer_sell:
                decision.battery_charge_power_w = min(battery_surplus_w, batt_max_w)
            decision.reason = f"{ev_reason} | Plan solar_charge {decision.battery_charge_power_w:.0f}W | {now_slot.reason}"

        elif now_slot.action == "grid_charge" and not econ_peak:
            decision.battery_charge_power_w = min(max(0.0, now_slot.target_power_w), batt_max_w)
            decision.reason = f"{ev_reason} | Plan grid_charge {decision.battery_charge_power_w:.0f}W | {now_slot.reason}"

        elif now_slot.action == "grid_charge" and econ_peak:
            if self_consume_ok:
                decision.battery_discharge_power_w = min(house_def_w, batt_max_w)
            decision.reason = (
                f"{ev_reason} | Plan grid_charge men economic_peak → egenförbrukning "
                f"{decision.battery_discharge_power_w:.0f}W | {now_slot.reason}"
            )

        elif now_slot.action == "export":
            batt_kwh = state.battery_soc_pct / 100.0 * state.battery_capacity_kwh
            floor_kwh = day_plan.export_floor_kwh
            if batt_kwh <= floor_kwh + 0.5:
                # Batteriet har nått exportgolvet – pausa urladdning
                decision.reason = (
                    f"{ev_reason} | Plan export PAUSAD – batteri {batt_kwh:.1f} kWh ≤ golv {floor_kwh:.1f} kWh"
                    f" | {now_slot.reason}"
                )
            else:
                decision.battery_discharge_power_w = min(
                    abs(now_slot.target_power_w) + house_def_w, batt_max_w
                )
                decision.reason = f"{ev_reason} | Plan export {decision.battery_discharge_power_w:.0f}W | {now_slot.reason}"

        else:  # idle / cover_load – egenförbrukning åt båda hållen
            if solar_surplus_w > 100:
                # Planen (upp till 15 min gammal prognos) väntade sig mörkt/lastat,
                # men verkligheten levererar överskott just nu – fånga det istället
                # för att exportera det för nästan inget. Klämmer mot REALTID,
                # inte mot planens prognos.
                battery_surplus_w = max(0.0, solar_surplus_w - ev_total_w)
                evening_fill = state.battery_soc_pct < evening_target
                prefer_sell = sell_price >= self.sell_solar_min_price and not evening_fill
                if not prefer_sell:
                    decision.battery_charge_power_w = min(battery_surplus_w, batt_max_w)
                decision.reason = (
                    f"{ev_reason} | Plan {now_slot.action} men verkligt solöverskott "
                    f"{decision.battery_charge_power_w:.0f}W | {now_slot.reason}"
                )
            else:
                if self_consume_ok:
                    decision.battery_discharge_power_w = min(house_def_w, batt_max_w)
                decision.reason = (
                    f"{ev_reason} | Plan {now_slot.action} egenförbrukning "
                    f"{decision.battery_discharge_power_w:.0f}W | {now_slot.reason}"
                )

        decision = self._apply_phase_limits(state, decision)

        _LOGGER.info(
            "Plan-executor kl %s: action=%s chg=%.0fW dis=%.0fW econ_peak=%s",
            now.strftime("%H:%M"), now_slot.action,
            decision.battery_charge_power_w, decision.battery_discharge_power_w, econ_peak,
        )
        return decision

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

        # Reduktionspass (max 4) – båda riktningar. Marginal mot den konfigurerade
        # fasgränsen eftersom regleringen är trög (30s cykel + Sonnens egen fördröjning).
        for iteration in range(4):
            any_violation = False
            for ph in PHASES:
                phase_current = loads[ph] / self.voltage
                if abs(phase_current) <= self.max_current_effective:
                    continue
                any_violation = True

                if phase_current < 0:
                    # Exportriktning – enda kontrollerbara källan är batteriurladdning
                    # (solproduktionen styr vi inte).
                    over_w = (abs(phase_current) - self.max_current_effective) * self.voltage
                    _LOGGER.warning(
                        "Pass %d: fas %s export överskriden %.1fA (max %.1fA)",
                        iteration, ph, abs(phase_current), self.max_current_effective,
                    )
                    if decision.battery_discharge_power_w > 0:
                        reduce_w_total = min(over_w * 3, decision.battery_discharge_power_w)
                        decision.battery_discharge_power_w -= reduce_w_total
                        for p in PHASES:
                            loads[p] += reduce_w_total / 3.0
                    continue

                over_w = (phase_current - self.max_current_effective) * self.voltage

                _LOGGER.warning(
                    "Pass %d: fas %s import överskriden %.1fA (max %.1fA)",
                    iteration, ph, phase_current, self.max_current_effective,
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
                    over_w = max(0, (loads[ph] / self.voltage - self.max_current_effective) * self.voltage)

                # Prio 2: batteriladdning
                if over_w > 0 and decision.battery_charge_power_w > 0:
                    reduce_w_total = min(over_w * 3, decision.battery_charge_power_w)
                    decision.battery_charge_power_w -= reduce_w_total
                    for p in PHASES:
                        loads[p] -= reduce_w_total / 3.0
                    over_w = max(0, (loads[ph] / self.voltage - self.max_current_effective) * self.voltage)

                # Prio 3: elpatron (om den är på – oavsett om den slogs på nu eller var på redan)
                if over_w > 0 and decision.extra_hot_water:
                    if ph in patron_phases:
                        for pp in patron_phases:
                            loads[pp] -= patron_per_phase_w
                        decision.extra_hot_water = False
                        _LOGGER.warning("Stänger av elpatron pga fasgräns på %s", ph)

            if not any_violation:
                break

        # Separat exporttak – växelriktarens AC-exportgräns är en annan begränsning
        # än fasströmmen. Klämmer bara batteriurladdningen, aldrig solproduktionen
        # (den styr vi inte).
        max_batt_export_w = max(0.0, self.max_export_w - state.solar_power_w)
        if decision.battery_discharge_power_w > max_batt_export_w:
            _LOGGER.debug(
                "Exporttak: urladdning %.0fW → %.0fW (sol %.0fW, tak %.0fW)",
                decision.battery_discharge_power_w, max_batt_export_w,
                state.solar_power_w, self.max_export_w,
            )
            decision.battery_discharge_power_w = max_batt_export_w

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
