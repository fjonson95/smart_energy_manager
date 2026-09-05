"""Dag-framåt energiplanerare för Smart Energy Manager.

Körs max var 15:e minut (eller när pris-/Solcast-data ändras).
Producerar ett DayPlan med tidsindexerade planslottar som körs PARALLELLT
med befintlig EnergyController – inga faktiska beslut ändras. Planeraren
loggar sin plan och varje avvikelse mot kontrollarens faktiska beslut.

Algoritm (tvåpass):
  1. Beräkna nyckeltal: exportgolv, kvällsmål, exporterbara kWh
  2. Fördela export prisväktat över mörka höga prisslots (kväll + morgon)
  3. Identifiera billiga nätladdningsstillfällen om batteri hamnar under mål
  4. Framåtsimulera batteri-SOC slot för slot
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .price_scheduler import PriceSchedule

_LOGGER = logging.getLogger(__name__)

_DARK_SOLAR_KW   = 2.0   # Soleffekt under detta → "mörk" slot
_PLAN_HORIZON_H  = 28    # Timmar framåt att planera
_HARD_FLOOR_FRACTION = 0.10  # Hårt golv: andel av batterikapaciteten som aldrig underskrids


def _uncertainty_markup(pv_production_ratio: float) -> float:
    """Osäkerhetspåslag på golvreserven baserat på produktionskvoten (P3-2).

    Kvot ~1.0 (normal produktion mot prognos) → litet baspåslag.
    Kvot ≤0.3 (snötäckta paneler etc.) → stort påslag, golvet mättas mot
    batteriets maxkapacitet ("full nattautonomi") via min()-klämningen i
    build_plan().
    """
    if pv_production_ratio >= 0.9:
        return 0.10
    if pv_production_ratio <= 0.3:
        return 3.0
    frac = (0.9 - pv_production_ratio) / (0.9 - 0.3)
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
            f"kvällsmål={self.evening_target_soc_pct:.0f}% "
            f"golv={self.export_floor_kwh:.1f}kWh "
            f"intäkt≈{self.expected_revenue_sek:.1f}kr"
        )


class EnergyPlanner:
    """Dag-framåt planerare som körs parallellt med EnergyController."""

    def __init__(
        self,
        battery_min_soc: float = 10.0,
        battery_max_soc: float = 95.0,
        export_sell_percentile: float = 0.80,
        export_min_sell_price_sek_kwh: float = 0.0,
        export_min_solar_tomorrow_kwh: float = 20.0,
        sell_solar_min_price: float = 0.80,
        cheap_charge_buy_percentile: float = 0.25,
        eta_roundtrip: float = 0.87,
        cycle_cost_sek_kwh: float = 0.05,
    ):
        self.battery_min_soc               = battery_min_soc
        self.battery_max_soc               = battery_max_soc
        self.export_sell_percentile        = export_sell_percentile
        self.export_min_sell_price         = export_min_sell_price_sek_kwh
        self.export_min_solar_tomorrow_kwh = export_min_solar_tomorrow_kwh
        self.sell_solar_min_price          = sell_solar_min_price
        self.cheap_charge_buy_percentile   = cheap_charge_buy_percentile
        self.eta_roundtrip                 = eta_roundtrip
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
    ) -> DayPlan:
        now_a = now if now.tzinfo else now.astimezone()
        horizon_end = now_a + timedelta(hours=_PLAN_HORIZON_H)

        batt_min_kwh = battery_capacity_kwh * self.battery_min_soc / 100.0
        batt_max_kwh = battery_capacity_kwh * self.battery_max_soc / 100.0
        batt_kwh     = battery_capacity_kwh * battery_soc_pct / 100.0
        _daily_avg_w = (predicted_daily_kwh / 24.0 * 1000.0) if predicted_daily_kwh > 0 else 0.0
        hourly_load_kw = max(_daily_avg_w, house_load_w, 500.0) / 1000.0

        # Golvformel (prognosreserv): behov_kwh täcker huslasten fram till solen
        # tar över, räknat mot PESSIMISTISK (p10) solprognos – ett underskattat
        # solvärde här gör att golvet blir för lågt och batteriet kan bli tomt.
        # Osäkerhetspåslaget skalas av produktionskvoten (P3-2): slår prognosen
        # fel (snö, nedsmutsning) höjs reserven, i värsta fall mot full
        # nattautonomi via min()-klämningen mot batt_max_kwh. Ett hårt golv
        # (10 % av kapaciteten) gäller alltid, oavsett hur liten reserven blir
        # en solig sommardag.
        takeover = solar_takeover_dt if (solar_takeover_dt and solar_takeover_dt > now_a) else now_a + timedelta(hours=9)
        takeover_local = takeover.astimezone()
        _floor_slots = [s for s in (ps.slots or []) if s.end > now_a and s.start < takeover_local]
        if _floor_slots:
            behov_kwh = sum(
                max(0.0, hourly_load_kw * (s.end - s.start).total_seconds() / 3600.0 - s.solar_kwh_p10)
                for s in _floor_slots
            ) + 2.0
        else:
            behov_kwh = hourly_load_kw * 9.0 + 2.0
        reserv_kwh = behov_kwh * (1.0 + _uncertainty_markup(pv_production_ratio))
        hard_floor_kwh = battery_capacity_kwh * _HARD_FLOOR_FRACTION
        export_floor_kwh = min(batt_max_kwh, max(hard_floor_kwh, reserv_kwh))
        evening_target_soc = min(self.battery_max_soc, self.battery_min_soc + export_floor_kwh / battery_capacity_kwh * 100.0)
        # Användbar energi ovan min_soc – energin under batt_min_kwh kan aldrig nås.
        exportable_kwh = max(0.0, batt_kwh - batt_min_kwh - export_floor_kwh)

        # Nettosol imorgon slot-för-slot – speglar controllerns nettosolkontroll.
        tomorrow_date = (now_a + timedelta(days=1)).date()
        # Exportspärren jämförs mot p10 (pessimistisk), aldrig p50 – den avgör
        # om vi vågar tömma batteriet ikväll i tillit till morgondagens sol.
        tomorrow_slots = [s for s in (ps.slots or []) if s.start.astimezone().date() == tomorrow_date]
        net_solar_tomorrow_kwh = (
            sum(
                max(0.0, s.solar_kwh_p10 - hourly_load_kw * (s.end - s.start).total_seconds() / 3600.0)
                for s in tomorrow_slots
            ) if tomorrow_slots
            else max(0.0, solar_forecast_tomorrow_kwh - hourly_load_kw * 13.0)
        )

        future_slots = [
            s for s in (ps.slots or [])
            if s.end > now_a and s.start < horizon_end
        ]
        if not future_slots:
            return DayPlan(
                generated_at=now_a, valid_until=now_a + timedelta(minutes=15),
                evening_target_soc_pct=evening_target_soc, export_floor_kwh=export_floor_kwh,
                total_exportable_kwh=exportable_kwh, expected_revenue_sek=0.0,
                solar_takeover_dt=solar_takeover_dt, hourly_load_kw=hourly_load_kw,
                pv_production_ratio=pv_production_ratio,
                notes="Inga prisslots tillgängliga",
            )

        # Exporttröskel: 80:e percentil av dagens säljpriser
        today_date = now_a.date()
        today_slots = [s for s in future_slots if s.start.astimezone().date() == today_date]
        sell_prices_sorted = sorted(s.sell_sek for s in (today_slots or future_slots))
        export_threshold = sell_prices_sorted[
            min(int(self.export_sell_percentile * len(sell_prices_sorted)), len(sell_prices_sorted) - 1)
        ]
        # Dispatch-fönstret använder alltid percentiltröskeln – abs-minimum är en trigger
        # för enstaka slots, inte en licens att skapa ett natt-långt billig-pris-fönster.
        eff_threshold = export_threshold

        # Export-slots: mörka, högt pris, inom 20h
        has_solar_data = any(s.solar_kw > 0 for s in future_slots)
        takeover_local = takeover.astimezone()
        window_end = now_a + timedelta(hours=20)
        high_slots = [
            s for s in future_slots
            if s.sell_sek >= eff_threshold
            and s.start < window_end
            and (s.solar_kw < _DARK_SOLAR_KW if has_solar_data
                 else s.start.astimezone() < takeover_local)
        ]

        # Marginalvärdesmodell (ersätter det gamla, aldrig-uppfyllbara köpris-filtret):
        # exportable_kwh är redan energin OVANFÖR golvet – den ersätts gratis av
        # morgondagens sol och har inget värde i sig självt utöver vad den kostade
        # att lagra. Sälj bara om säljpriset täcker batteriets faktiska snittpris
        # plus cykelkostnaden (slitage) – annars är det en förlustaffär.
        _min_export_value = battery_avg_cost_sek_kwh + self.cycle_cost_sek_kwh
        if _min_export_value > 0:
            high_slots = [s for s in high_slots if s.sell_sek > _min_export_value]

        # Prisväktad dispatch av exporterbara kWh
        export_plan: dict[datetime, float] = {}
        price_sum = sum(s.sell_sek for s in high_slots)
        can_export = (
            exportable_kwh > 0.1
            and net_solar_tomorrow_kwh >= export_floor_kwh
            and solar_forecast_tomorrow_kwh >= self.export_min_solar_tomorrow_kwh
        )
        if can_export and price_sum > 0:
            for s in high_slots:
                slot_h = (s.end - s.start).total_seconds() / 3600.0
                if slot_h <= 0:
                    continue
                w = (s.sell_sek / price_sum) * exportable_kwh / slot_h * 1000.0
                export_plan[s.start] = min(w, battery_max_power_kw * 1000.0)

        # Billiga nätladdningssots: bland mörka slots, lägsta 25%
        dark_slots = [s for s in future_slots if s.solar_kw < _DARK_SOLAR_KW and s.start not in export_plan]
        buy_prices = sorted(s.buy_sek for s in dark_slots)
        cheap_threshold = buy_prices[max(0, int(self.cheap_charge_buy_percentile * len(buy_prices)) - 1)] if buy_prices else 0.0
        cheap_set = {s.start for s in dark_slots if s.buy_sek <= cheap_threshold}

        # Framåtsimulering
        planned: list[PlannedSlot] = []
        expected_revenue = 0.0

        for slot in future_slots:
            slot_h = (slot.end - slot.start).total_seconds() / 3600.0
            if slot_h <= 0:
                continue

            soc_est = batt_kwh / battery_capacity_kwh * 100.0
            is_dark = slot.solar_kw < _DARK_SOLAR_KW
            solar_kwh = slot.solar_kw * slot_h
            load_kwh  = hourly_load_kw * slot_h

            action   = "idle"
            power_w  = 0.0
            reason   = ""

            if slot.start in export_plan:
                dispatch_w = export_plan[slot.start]
                avail = max(0.0, batt_kwh - batt_min_kwh)
                actual_w = min(dispatch_w, avail / slot_h * 1000.0, battery_max_power_kw * 1000.0)
                discharged = actual_w / 1000.0 * slot_h
                batt_kwh = max(batt_min_kwh, batt_kwh - discharged)
                expected_revenue += discharged * slot.sell_sek
                action  = "export"
                power_w = -actual_w
                reason  = f"sälj {slot.sell_sek:.2f} kr/kWh vikt {slot.sell_sek:.2f}/{price_sum:.2f}"

            elif not is_dark:
                surplus = max(0.0, solar_kwh - load_kwh)
                needs_kwh = max(0.0, export_floor_kwh - batt_kwh)
                room_kwh = max(0.0, batt_max_kwh - batt_kwh)

                if surplus > 0.01 and room_kwh > 0.1:
                    charge_kwh = min(surplus * 0.95, room_kwh, battery_max_power_kw * slot_h)
                    batt_kwh = min(batt_max_kwh, batt_kwh + charge_kwh)
                    power_w = min(charge_kwh / slot_h * 1000.0, battery_max_power_kw * 1000.0) if slot_h > 0 else 0.0
                    action = "solar_charge"
                    reason = (
                        f"sol {slot.solar_kw:.1f}kW → laddar (kvällsmål {needs_kwh:.1f}kWh kvar)"
                        if needs_kwh > 0.1
                        else f"sol {slot.solar_kw:.1f}kW → laddar mot max ({slot.sell_sek:.2f} kr)"
                    )
                elif surplus > 0.01:
                    action = "idle"
                    reason = f"sol {slot.solar_kw:.1f}kW → batteri fullt, sälj ({slot.sell_sek:.2f} kr)"
                else:
                    # Sol täcker inte lasten – självkonsumtion om batteri > golvet.
                    deficit_kwh = load_kwh - solar_kwh
                    avail_kwh = max(0.0, batt_kwh - batt_min_kwh - export_floor_kwh)
                    if avail_kwh > 0.01:
                        dis_kwh = min(deficit_kwh, avail_kwh, battery_max_power_kw * slot_h)
                        batt_kwh -= dis_kwh
                        power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                        action = "cover_load"
                        reason = f"sol {slot.solar_kw:.1f}kW < last {hourly_load_kw:.2f}kW → batteri {-power_w:.0f}W"
                    else:
                        action = "cover_load"
                        reason = f"sol {slot.solar_kw:.1f}kW < last {hourly_load_kw:.2f}kW → batteri vid golvet, nät"

            elif slot.start in cheap_set and batt_kwh < export_floor_kwh - 0.5:
                needed = min(export_floor_kwh - batt_kwh, battery_max_power_kw * slot_h)
                batt_kwh = min(batt_max_kwh, batt_kwh + needed)
                power_w = min(needed / slot_h * 1000.0, battery_max_power_kw * 1000.0) if slot_h > 0 else 0.0
                action = "grid_charge"
                reason = f"nätladda {slot.buy_sek:.2f} kr/kWh (gräns {cheap_threshold:.2f})"

            else:
                # Mörk slot utan export/nätladdning – självkonsumtion om batteri > golvet.
                # Speglar controllerns morgonlogik: sänk effektivt golv med 80% av sol inom 2h.
                deficit_kwh = max(0.0, load_kwh - solar_kwh)
                _next_2h_solar_kwh = sum(
                    s.solar_kw * (s.end - s.start).total_seconds() / 3600.0
                    for s in future_slots
                    if s.start >= slot.start and s.end <= slot.start + timedelta(hours=2) and s.solar_kw > 0
                )
                _eff_floor = max(0.0, export_floor_kwh - 0.8 * _next_2h_solar_kwh)
                avail_kwh = max(0.0, batt_kwh - batt_min_kwh - _eff_floor)
                if avail_kwh > 0.01 and deficit_kwh > 0.01:
                    dis_kwh = min(deficit_kwh, avail_kwh, battery_max_power_kw * slot_h)
                    batt_kwh -= dis_kwh
                    power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                    action = "cover_load"
                    solar_note = f" (sol {_next_2h_solar_kwh:.1f}kWh/2h)" if _next_2h_solar_kwh > 0.1 else ""
                    reason = f"mörk: självkonsumtion {-power_w:.0f}W batteri kvar {batt_kwh:.1f}kWh{solar_note}"
                else:
                    reason = f"mörk idle: batteri vid golvet, nät täcker → sälj={slot.sell_sek:.2f}"

            planned.append(PlannedSlot(
                start=slot.start.astimezone(),
                end=slot.end.astimezone(),
                action=action,
                target_power_w=power_w,
                battery_soc_est_pct=round(soc_est, 1),
                reason=reason,
            ))

        final_soc = batt_kwh / battery_capacity_kwh * 100.0
        morning_export = [s for s in planned if s.action == "export" and s.start.astimezone().date() > today_date]
        notes = (
            f"SOC {battery_soc_pct:.0f}% → {final_soc:.0f}% | "
            f"golv {export_floor_kwh:.1f}kWh netsol_imorgon {net_solar_tomorrow_kwh:.1f}kWh "
            f"pv_kvot={pv_production_ratio:.2f} | "
            f"trösklar export≥{eff_threshold:.2f} nätladdning≤{cheap_threshold:.2f} | "
            f"morgonexport: {len(morning_export)} slots"
        )

        return DayPlan(
            generated_at=now_a,
            valid_until=now_a + timedelta(minutes=15),
            evening_target_soc_pct=evening_target_soc,
            export_floor_kwh=export_floor_kwh,
            total_exportable_kwh=exportable_kwh,
            expected_revenue_sek=expected_revenue,
            solar_takeover_dt=solar_takeover_dt,
            hourly_load_kw=hourly_load_kw,
            pv_production_ratio=pv_production_ratio,
            slots=planned,
            notes=notes,
        )
