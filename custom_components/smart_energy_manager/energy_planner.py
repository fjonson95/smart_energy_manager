"""Dag-framåt energiplanerare för Smart Energy Manager.

Körs max var 15:e minut (eller när pris-/Solcast-data ändras).
Producerar ett DayPlan med tidsindexerade planslottar som körs PARALLELLT
med befintlig EnergyController – inga faktiska beslut ändras. Planeraren
loggar sin plan och varje avvikelse mot kontrollarens faktiska beslut.

Algoritm (v1.0 steg 3, "marginalvärdet V" – docs/v1_implementation_plan.md):
  1. Beräkna V: marginalvärdet av en kWh i batteriet just nu, från en
     merit-order-allokering av tillgänglig batterienergi mot framtida
     underskottsslots (dyrast pris först, begränsat av effekt och av vad
     solen hinner fylla på innan respektive slot).
  2. Framåtsimulera batteri-SOC slot för slot. Varje slot avgörs av fyra
     ömsesidigt uteslutande regler som alla jämför mot samma V – se
     _decide_slot().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .price_scheduler import PriceSchedule

_LOGGER = logging.getLogger(__name__)

_PLAN_HORIZON_H = 48  # v1.0 steg 3, punkt 5: 36-48h. Ingen prisprognos behövs
                       # utöver vad ps.slots redan har – morgondagens priser
                       # finns redan på kvällen när kvällsbesluten fattas.


# Andel av avståndet p10→p50 som räknas som "gratis solpåfyllning" i regel 3:s
# nätladdningsbehov. 0 = ren p10 (tidigare beteende), 1 = p50. Gäller BARA
# beslutet om hur mycket som ska köpas från nätet – reserver, V och
# merit-ordningens utrymmeskontroll använder fortfarande ren p10.
_GRID_CHARGE_SOLAR_BLEND = 0.5

_DROUGHT_STICK_TEMP_MIN_C = -5.0  # snö klibbar bara fast inom det här
_DROUGHT_STICK_TEMP_MAX_C = 5.0   # intervallet (användarens fysik, 2026-09-09)
                                   # – kallare och snön är lätt/pudrig, blåser
                                   # bort istället för att lägga sig på panelen.
_DROUGHT_RECOVERY_TEMP_C = 1.5    # dygnsmax över det här -> redan täckta
                                   # paneler glider/smälter fria. Uppmätt mot
                                   # riktig återhämtning (docs/
                                   # v1_implementation_plan.md): 15 jan 2026,
                                   # dygnsmax 2,6°C efter att 13-14 jan redan
                                   # nuddat 1,5-3,1°C.
_SNOWY_CONDITIONS = frozenset({"snowy", "snowy-rainy"})


def _simulate_drought_days(
    pv_production_ratio: float,
    current_outdoor_temp_c,
    weather_forecast,
) -> int:
    """Simulerar paneltillstånd (täckt/fri av snö) framåt och returnerar
    antal kommande dagar som förväntas förbli täckta (docs/
    v1_implementation_plan.md, "Torkrisk-påslag från SMHI:s väderprognos").

    weather_forecast: [(datum, condition, temp_max_c, temp_min_c), ...] för
    dagar bortom vad ps.slots redan täcker (SMHI:s dygnsprognos via
    weather.get_forecasts). Tom lista eller None → bara dagens
    startbedömning räknas (ingen väderentitet konfigurerad, exakt tidigare
    beteende om produktionskvoten dessutom är normal).

    Kyla i sig återtäcker ALDRIG panelerna – bara ny nederbörd (condition
    indikerar snö) inom klibbintervallet gör det. En redan täckt dag blir
    fri först när dygnsmax korsar återhämtningströskeln.
    """
    covered = (
        pv_production_ratio <= 0.3
        and current_outdoor_temp_c is not None
        and current_outdoor_temp_c <= 0.0
    )
    if not weather_forecast:
        return 1 if covered else 0

    drought_days = 1 if covered else 0
    for _day, condition, temp_max_c, temp_min_c in weather_forecast:
        if covered:
            if temp_max_c >= _DROUGHT_RECOVERY_TEMP_C:
                covered = False
                continue
            drought_days += 1
        else:
            snowy = condition in _SNOWY_CONDITIONS
            sticks = temp_min_c <= _DROUGHT_STICK_TEMP_MAX_C and temp_max_c >= _DROUGHT_STICK_TEMP_MIN_C
            if snowy and sticks:
                covered = True
                drought_days += 1
    return drought_days


def _drought_markup(drought_days_ahead: int) -> float:
    """Prospektivt riskpåslag på V av samma sort och tak som
    _uncertainty_markup – anropsstället tar max() av de två, aldrig summan,
    så den nya signalen aldrig kan stapla förbi ett redan verifierat säkert
    tak (3,0×). Skalningen (0,5/dag) är en startgissning, kalibrerad mot
    backtest i verifieringssteget, inte huggen i sten (docs/
    v1_implementation_plan.md).
    """
    if drought_days_ahead <= 0:
        return 0.0
    return min(3.0, drought_days_ahead * 0.5)


def _uncertainty_markup(pv_production_ratio: float) -> float:
    """Riskpåslag på V baserat på produktionskvoten (P3-2).

    Kvot ≥0.6 (normal produktion, inkl. höstens prognosbrus) → litet baspåslag.
    Kvot ≤0.3 (snötäckta paneler etc.) → stort påslag – ett högre V ger
    mindre export, sparsammare självkonsumtion och nätladdning vid högre
    priser, alla tre effekterna man vill ha när solprognosen inte går att
    lita på (v1.0 steg 3, punkt 3 – flyttad hit från golvbanan i steg 2).
    Rampen började vid 0,9 och gav +40–87 % vid kvot 0,74–0,84 i oktober –
    batteriet stod fullt medan huset köpte för 3–4 kr (docs/decisions.md 2026-10-29).
    """
    if pv_production_ratio >= 0.6:
        return 0.10
    if pv_production_ratio <= 0.3:
        return 3.0
    frac = (0.6 - pv_production_ratio) / (0.6 - 0.3)
    return 0.10 + frac * (3.0 - 0.10)


@dataclass
class PlannedSlot:
    start: datetime
    end: datetime
    # "export" | "solar_charge" | "grid_charge" | "idle" | "cover_load"
    action: str
    target_power_w: float       # Positiv = laddning, negativ = urladdning
    battery_soc_est_pct: float  # Estimerad SOC vid slottens START
    reason: str
    ev_charge_w: float = 0.0    # v1.0 steg 6: schemalagd EV-laddeffekt denna slot, 0 = ingen
    # Planen har medvetet valt att köpa från nätet och spara batteriet denna
    # slot (regel 2: köp ≤ sparvärde, eller kapaciteten är öronmärkt åt en
    # bättre slot). Exekutorn får då inte täcka huslasten ur batteriet.
    hold_battery: bool = False


@dataclass
class DayPlan:
    generated_at: datetime
    valid_until: datetime

    evening_target_soc_pct: float
    export_floor_kwh: float
    total_exportable_kwh: float
    expected_revenue_sek: float
    solar_takeover_dt: Optional[datetime]
    hourly_load_kw: float
    pv_production_ratio: float = 1.0
    marginal_value_sek_kwh: float = 0.0
    marginal_slot_start: Optional[datetime] = None
    # v1.0 steg 7 punkt 6: V_charge (regel 1:s faktiska tröskel, inte rå V) -
    # exponerad separat så heat_planner.should_dump_to_hot_water() kan använda
    # exakt samma jämförelse som avgör om batteriet laddas från solöverskott.
    marginal_value_charge_sek_kwh: float = 0.0

    slots: list[PlannedSlot] = field(default_factory=list)
    notes: str = ""

    def slot_at(self, dt: datetime) -> Optional[PlannedSlot]:
        dt_aware = dt if dt.tzinfo else dt.astimezone()
        for s in self.slots:
            if s.start <= dt_aware < s.end:
                return s
        return None

    def summary(self) -> str:
        n_exp   = sum(1 for s in self.slots if s.action == "export")
        n_sol   = sum(1 for s in self.slots if s.action == "solar_charge")
        n_grid  = sum(1 for s in self.slots if s.action == "grid_charge")
        exp_kwh = sum(
            abs(s.target_power_w) / 1000.0 * (s.end - s.start).total_seconds() / 3600.0
            for s in self.slots if s.action == "export"
        )
        return (
            f"DayPlan@{self.generated_at.strftime('%H:%M')}: "
            f"export={n_exp}×slot/{exp_kwh:.1f}kWh "
            f"sol_laddning={n_sol} nätladdning={n_grid} "
            f"V={self.marginal_value_sek_kwh:.2f}kr/kWh "
            f"reserv={self.export_floor_kwh:.1f}kWh "
            f"intäkt≈{self.expected_revenue_sek:.1f}kr"
        )


class EnergyPlanner:
    """Dag-framåt planerare som körs parallellt med EnergyController."""

    def __init__(
        self,
        battery_min_soc: float = 10.0,
        battery_max_soc: float = 95.0,
        eta_roundtrip: float = 0.87,
        cycle_cost_sek_kwh: float = 0.05,
    ):
        self.battery_min_soc               = battery_min_soc
        self.battery_max_soc               = battery_max_soc
        self.eta_roundtrip                = eta_roundtrip
        self.cycle_cost_sek_kwh            = cycle_cost_sek_kwh

    def build_plan(
        self,
        now: datetime,
        battery_soc_pct: float,
        battery_capacity_kwh: float,
        battery_max_power_kw: float,
        ps: PriceSchedule,
        predicted_daily_kwh: float,
        solar_forecast_tomorrow_kwh: float,
        solar_takeover_dt: Optional[datetime],
        house_load_w: float = 0.0,
        battery_avg_cost_sek_kwh: float = 0.0,
        pv_production_ratio: float = 1.0,
        yesterday_consumption_kwh: Optional[float] = None,
        house_load_avg_w: Optional[float] = None,
        ev_reserve_margin_kwh: float = 0.0,
        rolling_consumption_kwh: Optional[float] = None,
        load_shape_p50: Optional[list] = None,
        load_shape_p75: Optional[list] = None,
        ev_energy_needed_kwh: float = 0.0,
        ev_deadline: Optional[datetime] = None,
        ev_max_power_kw: float = 0.0,
        current_outdoor_temp_c: Optional[float] = None,
        weather_forecast: Optional[list] = None,
        daily_load_kwh_by_date: Optional[dict] = None,
    ) -> DayPlan:
        # solar_forecast_tomorrow_kwh/solar_takeover_dt används inte längre (V ser morgondagens sol via ps.slots). Se docs/decisions.md P01.
        now_a = now if now.tzinfo else now.astimezone()
        horizon_end = now_a + timedelta(hours=_PLAN_HORIZON_H)

        batt_min_kwh = battery_capacity_kwh * self.battery_min_soc / 100.0
        batt_max_kwh = battery_capacity_kwh * self.battery_max_soc / 100.0
        batt_kwh     = battery_capacity_kwh * battery_soc_pct / 100.0
        # Gradtimmodellen täcker inte baslasten – max mot verklig förbrukning (7-dygnssnitt före gårdagen). Se docs/decisions.md P02.
        _consumption_floor_kwh = rolling_consumption_kwh or yesterday_consumption_kwh or 0.0
        _eff_daily_kwh = max(predicted_daily_kwh, _consumption_floor_kwh)
        _load_for_projection_w = house_load_avg_w if house_load_avg_w is not None else house_load_w
        hourly_load_kw = max(_eff_daily_kwh / 24.0, _load_for_projection_w / 1000.0, 0.5)

        # Med väderprognos får varje dygn sin egen nivå: en köldknäpp i natt
        # syns i planen redan i kväll, inte först dagen efter (gradtimmodellen
        # matades annars bara med gårdagens/dämpad temperatur).
        def _eff_daily_kwh_on(dt: datetime) -> float:
            if daily_load_kwh_by_date:
                level = daily_load_kwh_by_date.get(dt.astimezone().date())
                if level is not None:
                    return max(level, _consumption_floor_kwh)
            return _eff_daily_kwh

        # Formen skalas mot _eff_daily_kwh, inte rå predicted_daily_kwh (mild dag gav nästan noll last, 2026-09-23). Se docs/decisions.md P03.
        def _load_kw_at(dt: datetime, shape: Optional[list]) -> float:
            if shape is not None:
                h = shape[dt.astimezone().hour]
                if h is not None:
                    return _eff_daily_kwh_on(dt) * h
            return max(_eff_daily_kwh_on(dt) / 24.0, hourly_load_kw)

        def _slot_load_kwh(s, shape: Optional[list]) -> float:
            return _load_kw_at(s.start, shape) * (s.end - s.start).total_seconds() / 3600.0

        # Icke-avtagande säkerhetsmarginal: alltid utom räckhåll för V:s
        # allokering och för självkonsumtion, oavsett pris. 2 kWh fast
        # buffert + EV-marginal (en vald bil som kan behöva ladda under det
        # mörka fönstret utan att golvet räknar som om bilen inte fanns) –
        # bevarad från steg 2, den delen av golvbanan V INTE ersätter (V
        # prisar bara den energi som FÅR spenderas, den här bufferten är
        # energi som aldrig är till salu, till något pris).
        _flat_buffer_kwh = 2.0 + max(0.0, ev_reserve_margin_kwh)

        future_slots = [
            s for s in (ps.slots or [])
            if s.end > now_a and s.start < horizon_end
        ]

        def _empty_plan(notes: str) -> DayPlan:
            _reserved_kwh = batt_min_kwh + _flat_buffer_kwh
            return DayPlan(
                generated_at=now_a, valid_until=now_a + timedelta(minutes=15),
                evening_target_soc_pct=min(self.battery_max_soc, self.battery_min_soc + _flat_buffer_kwh / battery_capacity_kwh * 100.0),
                export_floor_kwh=_reserved_kwh,
                total_exportable_kwh=max(0.0, batt_kwh - _reserved_kwh),
                expected_revenue_sek=0.0,
                solar_takeover_dt=solar_takeover_dt, hourly_load_kw=hourly_load_kw,
                pv_production_ratio=pv_production_ratio,
                notes=notes,
            )

        if not future_slots:
            return _empty_plan("Inga prisslots tillgängliga")

        # V via merit-order; _cap_by_time på p10/P75 (p50 gav cirkulär regression), _banked_extra_kwh vid fullt batteri. Se docs/decisions.md P04.
        _cap_by_time: dict[datetime, float] = {}
        _running_cap = batt_kwh
        _banked_extra_kwh = 0.0
        for s in future_slots:
            s_h = (s.end - s.start).total_seconds() / 3600.0
            if s_h <= 0:
                continue
            _s_surplus_kwh = max(0.0, s.solar_kwh_p10 - _slot_load_kwh(s, load_shape_p75))
            _increment = min(_s_surplus_kwh, battery_max_power_kw * s_h)
            _headroom = max(0.0, batt_max_kwh - _running_cap)
            if _increment > _headroom:
                _banked_extra_kwh += _increment - _headroom
                _running_cap = batt_max_kwh
            else:
                _running_cap += _increment
            _cap_by_time[s.start] = _running_cap + _banked_extra_kwh

        # Veckogenomgång 2026-10-10: regel 3 nätladdade 20 kWh natten före en
        # solig dag (9/10, prognos p50 43,6 / p10 12,1 kWh, utfall 40,1) och
        # 11 kWh exporterades sedan för 0,31 kr. Veckans p50-prognoser låg
        # bara ~5 % över utfallet - ren p10 var för pessimistisk för just
        # köpbeslutet (extra el kostar en kväll, inte hela reserven).
        _fill_gain_kwh = 0.0
        for s in future_slots:
            s_h = (s.end - s.start).total_seconds() / 3600.0
            if s_h <= 0:
                continue
            _blend_kwh = s.solar_kwh_p10 + _GRID_CHARGE_SOLAR_BLEND * max(0.0, s.solar_kwh - s.solar_kwh_p10)
            _s_fill = max(0.0, _blend_kwh - _slot_load_kwh(s, load_shape_p75))
            _fill_gain_kwh += min(_s_fill, battery_max_power_kw * s_h)
        _fill_cap_end_kwh = batt_kwh + _fill_gain_kwh

        _reserved_kwh = batt_min_kwh + _flat_buffer_kwh

        # Utan morgondagens priser: höj golvet med Solcasts kända underskott tills priserna kommer (2026-09-22). Se docs/decisions.md P05.
        _tomorrow_start = (now_a + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        _prices_cover_tomorrow = any(s.start >= _tomorrow_start for s in future_slots)
        if not _prices_cover_tomorrow:
            _tomorrow_deficit_kwh = max(0.0, _eff_daily_kwh_on(_tomorrow_start + timedelta(hours=12)) - solar_forecast_tomorrow_kwh)
            _reserved_kwh = max(_reserved_kwh, min(batt_max_kwh, batt_min_kwh + _tomorrow_deficit_kwh))

        # En gemensam merit-order över täckning och export – två separata pass kunde ge V=0. Se docs/decisions.md P06.
        _opportunities: list[tuple] = []  # (slot, värde_sek_kwh, kwh)
        for s in future_slots:
            s_h = (s.end - s.start).total_seconds() / 3600.0
            if s_h <= 0:
                continue
            cap_kwh = battery_max_power_kw * s_h
            deficit = max(0.0, _slot_load_kwh(s, load_shape_p75) - s.solar_kwh_p10)
            tier1 = min(deficit, cap_kwh)
            if tier1 > 0.01:
                _opportunities.append((s, s.buy_sek, tier1))
            rest_kwh = cap_kwh - tier1
            if rest_kwh > 0.01:
                _opportunities.append((s, s.sell_sek, rest_kwh))

        # En kandidat måste rymmas under taket vid varje senare tidpunkt (värdeordning ≠ tidsordning). Se docs/decisions.md P07.
        _slot_starts_sorted = sorted({s.start for s in future_slots})
        _withdrawn_at: dict[datetime, float] = {t: 0.0 for t in _slot_starts_sorted}

        def _slack_min_from(t_i: datetime) -> float:
            running = sum(_withdrawn_at[t] for t in _slot_starts_sorted if t < t_i)
            min_slack = float("inf")
            for t in _slot_starts_sorted:
                if t < t_i:
                    continue
                running += _withdrawn_at[t]
                slack = _cap_by_time.get(t, batt_kwh) - _reserved_kwh - running
                if slack < min_slack:
                    min_slack = slack
            return min_slack if min_slack != float("inf") else 0.0

        _accepted: list[tuple] = []  # (slot, värde, kwh)
        for s, value, cap_kwh in sorted(_opportunities, key=lambda x: x[1], reverse=True):
            _avail_at_t = max(0.0, _slack_min_from(s.start))
            _alloc = min(cap_kwh, _avail_at_t)
            if _alloc > 0.01:
                _withdrawn_at[s.start] += _alloc
                _accepted.append((s, value, _alloc))

        # Regel 4 begränsas till det merit-ordern tilldelat den här sloten som export. Se docs/decisions.md P08.
        _accepted_export_kwh_by_slot: dict[datetime, float] = {}
        for _s, _value, _kwh in _accepted:
            if _value == _s.sell_sek:
                _accepted_export_kwh_by_slot[_s.start] = _accepted_export_kwh_by_slot.get(_s.start, 0.0) + _kwh

        # Regel 2 begränsas till det som öronmärkts åt sloten (natten åt morgontoppens kapacitet, 2026-09-16). Se docs/decisions.md P09.
        _accepted_cover_kwh_by_slot: dict[datetime, float] = {}
        for _s, _value, _kwh in _accepted:
            if _value == _s.buy_sek:
                _accepted_cover_kwh_by_slot[_s.start] = _accepted_cover_kwh_by_slot.get(_s.start, 0.0) + _kwh

        if _accepted:
            _cheapest = min(_accepted, key=lambda x: x[1])
            _v_raw = _cheapest[1]
            _marginal_slot_start = _cheapest[0].start
        elif _opportunities:
            # Möjligheter finns, men INGEN kunde tilldelas – batteriet är
            # redan under den bevarade reserven (t.ex. en historisk
            # startpunkt lägre än den konfigurerade reserven) med ingen sol
            # i sikte för att lyfta taket. Maximal knapphet: V sätts av den
            # mest värdefulla obetjänade möjligheten, vilket i praktiken
            # blockerar all vidare urladdning/export och gynnar nätladdning
            # (regel 3) tills reserven är återställd.
            _v_raw = max(value for _, value, _ in _opportunities)
            _marginal_slot_start = None
        else:
            _v_raw = 0.0
            _marginal_slot_start = None

        # Torkrisken höjer V via max() inom samma tak som _uncertainty_markup. Se docs/decisions.md P10.
        _drought_days_ahead = _simulate_drought_days(pv_production_ratio, current_outdoor_temp_c, weather_forecast)
        V = _v_raw * (1.0 + max(_uncertainty_markup(pv_production_ratio), _drought_markup(_drought_days_ahead)))
        # Laddning kräver en η-strängare tröskel; V_charge utan torkpåslag (gjorde nätladdningen för ivrig). Se docs/decisions.md P11.
        V_charge = _v_raw * (1.0 + _uncertainty_markup(pv_production_ratio)) * self.eta_roundtrip

        evening_target_soc = min(self.battery_max_soc, self.battery_min_soc + _flat_buffer_kwh / battery_capacity_kwh * 100.0)
        exportable_kwh = max(0.0, batt_kwh - _reserved_kwh)

        # Prioritetsordning för dagens solöverskott, per sammanhängande dagsljusfönster. Se docs/decisions.md P12.
        _charge_priority_kwh: dict[datetime, float] = {}
        _charge_candidates = []
        _seen_surplus = False
        for s in future_slots:
            s_h = (s.end - s.start).total_seconds() / 3600.0
            if s_h <= 0:
                continue
            _s_load_kw = _load_kw_at(s.start, load_shape_p50)
            _s_surplus_kwh = max(0.0, s.solar_kw * s_h - _s_load_kw * s_h)
            _s_deficit_kwh = max(0.0, _s_load_kw * s_h - s.solar_kw * s_h)
            if _s_surplus_kwh > 0.01:
                _seen_surplus = True
                if V_charge > s.sell_sek:
                    _charge_candidates.append((s, _s_surplus_kwh, s_h))
            elif _s_deficit_kwh > 0.01 and _seen_surplus:
                # Natten efter dagens fönster – stoppa här, inte förrän hit
                # (leden ovanför hoppar bara över eventuella mörka slots
                # INNAN dagens fönster hunnit börja, t.ex. om det är natt nu).
                break
        _room_remaining_kwh = max(0.0, batt_max_kwh - batt_kwh)
        for s, _surplus_kwh, s_h in sorted(_charge_candidates, key=lambda x: x[0].sell_sek):
            _cap_kwh = min(_surplus_kwh * 0.95, battery_max_power_kw * s_h, _room_remaining_kwh)
            if _cap_kwh > 0.01:
                _charge_priority_kwh[s.start] = _cap_kwh
                _room_remaining_kwh -= _cap_kwh

        # --- Framåtsimulering ----------------------------------------------
        planned: list[PlannedSlot] = []
        expected_revenue = 0.0
        _initial_batt_kwh = batt_kwh
        # Regel 3-kandidater samlas och allokeras billigast först (2026-09-15). Se docs/decisions.md P13.
        _grid_charge_candidates: list[tuple] = []

        for slot in future_slots:
            slot_h = (slot.end - slot.start).total_seconds() / 3600.0
            if slot_h <= 0:
                continue

            soc_est = batt_kwh / battery_capacity_kwh * 100.0
            _slot_load_kw = _load_kw_at(slot.start, load_shape_p50)
            solar_kwh = slot.solar_kw * slot_h
            load_kwh  = _slot_load_kw * slot_h
            surplus_kwh = max(0.0, solar_kwh - load_kwh)
            deficit_kwh = max(0.0, load_kwh - solar_kwh)

            action  = "idle"
            power_w = 0.0
            reason  = ""
            hold_battery = False

            # Regel 1 (V > sälj_nu → spara): bara relevant när det finns
            # solöverskott att ta ställning till. Annars faller överskottet
            # rakt igenom till nätet (mätarens egen export), ingen batteri-
            # åtgärd krävs för att det ska säljas.
            if surplus_kwh > 0.01:
                room_kwh = max(0.0, batt_max_kwh - batt_kwh)
                _priority_kwh = _charge_priority_kwh.get(slot.start, 0.0)
                if V_charge > slot.sell_sek and room_kwh > 0.1 and _priority_kwh > 0.01:
                    charge_kwh = min(surplus_kwh * 0.95, room_kwh, battery_max_power_kw * slot_h, _priority_kwh)
                    batt_kwh = min(batt_max_kwh, batt_kwh + charge_kwh)
                    power_w = min(charge_kwh / slot_h * 1000.0, battery_max_power_kw * 1000.0) if slot_h > 0 else 0.0
                    action = "solar_charge"
                    reason = f"sol {slot.solar_kw:.1f}kW, V·η {V_charge:.2f}>sälj {slot.sell_sek:.2f} → spara (prio {_priority_kwh:.1f}kWh)"
                elif V_charge > slot.sell_sek and room_kwh > 0.1:
                    reason = f"sol {slot.solar_kw:.1f}kW → sälj direkt (rummet prioriterat åt billigare timmar)"
                else:
                    reason = f"sol {slot.solar_kw:.1f}kW → sälj direkt (V·η {V_charge:.2f}≤sälj {slot.sell_sek:.2f})"

            # Regel 2 (köp_nu > V → täck från batteri): bara relevant när
            # solen inte räcker till lasten. Cykelkostnaden läggs på här
            # också (konsekvent med regel 3/4) – "slitagekostnad per cyklad
            # kWh" gäller lika mycket när batteriet töms för egenförbrukning
            # som när det töms för export; annars blir tröskeln inkonsekvent
            # mellan de fyra reglerna för samma fysiska händelse (urladdning).
            elif deficit_kwh > 0.01:
                if slot.buy_sek > V + self.cycle_cost_sek_kwh:
                    avail = max(0.0, batt_kwh - _reserved_kwh)
                    # Öronmärkt åt DEN HÄR sloten (se _accepted_cover_kwh_by_slot
                    # ovan) - annars äter vanlig självkonsumtion timme för timme
                    # upp kapacitet merit-ordern redan reserverat åt en dyrare
                    # möjlighet senare (t.ex. morgontoppen).
                    _accepted_kwh = _accepted_cover_kwh_by_slot.get(slot.start, 0.0)
                    if avail > 0.01 and _accepted_kwh > 0.01:
                        dis_kwh = min(deficit_kwh, avail, battery_max_power_kw * slot_h, _accepted_kwh)
                        batt_kwh -= dis_kwh
                        power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                        action = "cover_load"
                        reason = (
                            f"Nätpris {slot.buy_sek:.2f} kr > batteriets sparvärde {V:.2f} kr "
                            f"→ laddar ur batteriet {-power_w:.0f}W ({_accepted_kwh:.1f}/{avail:.1f}kWh tillgängligt)"
                        )
                    else:
                        action = "cover_load"
                        hold_battery = True
                        reason = (
                            f"Nätpris {slot.buy_sek:.2f} kr > sparvärde {V:.2f} kr, men batteriet är "
                            f"reserverat åt ett bättre tillfälle → köper från nätet istället"
                        )
                else:
                    hold_battery = True
                    reason = (
                        f"Nätpris {slot.buy_sek:.2f} kr < batteriets sparvärde {V:.2f} kr "
                        f"→ köper från nätet, sparar batteriet"
                    )
            else:
                reason = "balans: sol täcker last"

            # Regel 3 (köp_nu + cykel < V → nätladda) och regel 4 (sälj_nu >
            # V + cykel → exportera): rena arbitragebeslut, oberoende av om
            # sloten redan har sol/last-obalans – gäller bara om regel 1/2
            # inte redan avgjorde sloten. Regel 4 kräver dessutom att det
            # inte finns ett obetalt underskott den här sloten (annars säljs
            # och köps samtidigt, alltid en förlustaffär eftersom köp>sälj).
            if action == "idle":
                room_kwh = max(0.0, batt_max_kwh - batt_kwh)
                # Nätladda bara det solen inte väntas fylla inom horisonten (2026-09-10). Se docs/decisions.md P14.
                _solar_fills_kwh = max(0.0, _fill_cap_end_kwh - batt_kwh)
                _grid_needed_kwh = max(0.0, room_kwh - _solar_fills_kwh)
                if slot.buy_sek + self.cycle_cost_sek_kwh < V_charge and _grid_needed_kwh > 0.1:
                    # Laddas INTE här - se _grid_charge_candidates-kommentaren
                    # ovanför huvudloopen. batt_kwh/action/power_w lämnas
                    # orörda tills det billigast-först-passet nedan.
                    _grid_charge_candidates.append((slot, slot_h, _grid_needed_kwh, len(planned)))
                # max(V, battery_avg_cost) hindrar export under vad energin kostade. Se docs/decisions.md P15.
                elif deficit_kwh <= 0.01 and slot.sell_sek > max(V, battery_avg_cost_sek_kwh) + self.cycle_cost_sek_kwh:
                    avail = max(0.0, batt_kwh - _reserved_kwh)
                    # Exportera bara det merit-ordern faktiskt tilldelade
                    # DEN HÄR sloten som exportmöjlighet (se
                    # _accepted_export_kwh_by_slot ovan) – inte allt
                    # fysiskt tillgängligt. Merit-ordern har redan sett
                    # hela horisonten och kan ha reserverat samma kapacitet
                    # åt en bättre säljmöjlighet senare (t.ex. kvällstoppen)
                    # även om dagens pris också klarar tröskeln.
                    _accepted_kwh = _accepted_export_kwh_by_slot.get(slot.start, 0.0)
                    dis_kwh = min(avail, battery_max_power_kw * slot_h, _accepted_kwh)
                    if dis_kwh > 0.01:
                        batt_kwh -= dis_kwh
                        power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                        action = "export"
                        expected_revenue += dis_kwh * slot.sell_sek
                        reason = f"exportera {slot.sell_sek:.2f}>max(V {V:.2f}, snittkostnad {battery_avg_cost_sek_kwh:.2f})+cykel (merit-order {_accepted_kwh:.1f}/{avail:.1f}kWh)"

            planned.append(PlannedSlot(
                start=slot.start.astimezone(),
                end=slot.end.astimezone(),
                action=action,
                target_power_w=power_w,
                battery_soc_est_pct=round(soc_est, 1),
                reason=reason,
                hold_battery=hold_battery,
            ))

        # Regel 3, andra passet: allokera insamlade nätladdningskandidater
        # BILLIGAST FÖRST, inte i kronologisk ordning (se kommentaren vid
        # _grid_charge_candidates ovan). Totalbehovet tas som det STÖRSTA
        # observerade _grid_needed_kwh bland kandidaterna - samma golv som
        # regel 3 redan räknade per slot, bara samlat till en gemensam pott
        # istället för att spenderas i tidsordning.
        if _grid_charge_candidates:
            _grid_total_need_kwh = max(c[2] for c in _grid_charge_candidates)
            _grid_remaining_kwh = _grid_total_need_kwh
            for _cand_slot, _cand_slot_h, _, _cand_idx in sorted(
                _grid_charge_candidates, key=lambda c: c[0].buy_sek,
            ):
                if _grid_remaining_kwh <= 0.1:
                    break
                _charge_kwh = min(_grid_remaining_kwh, battery_max_power_kw * _cand_slot_h)
                if _charge_kwh <= 0.01:
                    continue
                _power_w = (
                    min(_charge_kwh / _cand_slot_h * 1000.0, battery_max_power_kw * 1000.0)
                    if _cand_slot_h > 0 else 0.0
                )
                _p = planned[_cand_idx]
                _p.action = "grid_charge"
                _p.hold_battery = False
                _p.target_power_w = _power_w
                _p.reason = (
                    f"nätladda {_cand_slot.buy_sek:.2f}+cykel<V·η {V_charge:.2f} "
                    f"(billigast-först {_charge_kwh:.1f}/{_grid_total_need_kwh:.1f}kWh)"
                )
                _grid_remaining_kwh -= _charge_kwh

            # Slutgiltig SOC-bana: räkna om battery_soc_est_pct för varje slot
            # från grunden nu när nätladdningen kan ha flyttats i tiden -
            # annars visar slots EFTER en omplacerad laddning fel SOC (de
            # räknades ut i förhållande till den ursprungliga, kronologiska
            # ordningen i huvudloopen ovan).
            _replay_kwh = _initial_batt_kwh
            for _p in planned:
                _p.battery_soc_est_pct = round(_replay_kwh / battery_capacity_kwh * 100.0, 1)
                _replay_slot_h = (_p.end - _p.start).total_seconds() / 3600.0
                if _replay_slot_h > 0:
                    _replay_kwh += (_p.target_power_w / 1000.0) * _replay_slot_h
                _replay_kwh = max(0.0, min(batt_max_kwh, _replay_kwh))
            # batt_kwh används fortfarande nedanför (t.ex. final_soc) - måste
            # spegla den nätladdning pass två la till, annars saknas den.
            batt_kwh = _replay_kwh

        # EV-schemat är en fristående merit-order mot deadline och undviker batteriets grid_charge-slots. Se docs/decisions.md P16.
        if ev_energy_needed_kwh > 0.01 and ev_max_power_kw > 0.01:
            _buy_by_start = {s.start: s.buy_sek for s in future_slots}
            _ev_deadline_dt = ev_deadline if (ev_deadline and ev_deadline > now_a) else horizon_end
            _ev_candidates = [
                p for p in planned
                if p.start < _ev_deadline_dt and p.action != "grid_charge"
            ]
            _ev_remaining_kwh = ev_energy_needed_kwh
            _ev_charge_kwh: dict[datetime, float] = {}
            for p in sorted(_ev_candidates, key=lambda p: _buy_by_start.get(p.start, float("inf"))):
                if _ev_remaining_kwh <= 0.01:
                    break
                p_h = (p.end - p.start).total_seconds() / 3600.0
                if p_h <= 0:
                    continue
                _alloc_kwh = min(ev_max_power_kw * p_h, _ev_remaining_kwh)
                if _alloc_kwh > 0.01:
                    _ev_charge_kwh[p.start] = _alloc_kwh / p_h * 1000.0  # → W
                    _ev_remaining_kwh -= _alloc_kwh
            for p in planned:
                if p.start in _ev_charge_kwh:
                    p.ev_charge_w = _ev_charge_kwh[p.start]

        final_soc = batt_kwh / battery_capacity_kwh * 100.0
        notes = (
            f"SOC {battery_soc_pct:.0f}% → {final_soc:.0f}% | "
            f"V={V:.2f}kr/kWh (rå {_v_raw:.2f}, pv_kvot={pv_production_ratio:.2f}) "
            f"satt av {_marginal_slot_start.astimezone().strftime('%d %H:%M') if _marginal_slot_start else 'bästa säljpris'} "
            f"(torkdagar_fram={_drought_days_ahead}) | "
            f"reserv {_reserved_kwh:.1f}kWh exporterbart {exportable_kwh:.1f}kWh"
        )

        return DayPlan(
            generated_at=now_a,
            valid_until=now_a + timedelta(minutes=15),
            evening_target_soc_pct=evening_target_soc,
            export_floor_kwh=_reserved_kwh,
            total_exportable_kwh=exportable_kwh,
            expected_revenue_sek=expected_revenue,
            solar_takeover_dt=solar_takeover_dt,
            hourly_load_kw=hourly_load_kw,
            pv_production_ratio=pv_production_ratio,
            marginal_value_sek_kwh=V,
            marginal_value_charge_sek_kwh=V_charge,
            marginal_slot_start=_marginal_slot_start,
            slots=planned,
            notes=notes,
        )
