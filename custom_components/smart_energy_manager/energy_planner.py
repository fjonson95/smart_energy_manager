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

_PLAN_HORIZON_H  = 28    # Timmar framåt att planera
_HARD_FLOOR_FRACTION = 0.10  # Hårt golv: andel av batterikapaciteten som aldrig underskrids

# --- Interimslösning ("Option B", docs/forbrukningsanalys.md) -------------
# Prisspärren nedan är MEDVETET en enkel första version, inte den slutgiltiga
# lösningen. Den öppnar golvet rakt av mot hard_floor_kwh varje gång villkoret
# slår till, utan gräns för hur mycket av marginalen som spenderas – testat
# mot ett "0 sol imorgon"-scenario visade att den kan tömma batteriet till
# hard_floor timmar innan nästa kvällstopp om solprognosen slår fel. Två
# säkrare varianter (dynamisk percentiltröskel; smalare relaxation) är
# skisserade men inte implementerade. Håll koll på det här avsnittet när den
# riktiga lösningen bestäms. Osäkerheten i solprognosen väger INTE in här
# längre (v1.0 steg 0) – den hanteras redan av _uncertainty_markup() som
# höjer själva golvet; att dessutom stänga prisspärren vid låg pv_kvot
# slog igen den precis de dygn (snötäckta paneler) reserven finns till för.


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
        yesterday_consumption_kwh: Optional[float] = None,
        house_load_avg_w: Optional[float] = None,
        ev_reserve_margin_kwh: float = 0.0,
        rolling_consumption_kwh: Optional[float] = None,
        load_shape_p50: Optional[list] = None,
        load_shape_p75: Optional[list] = None,
    ) -> DayPlan:
        now_a = now if now.tzinfo else now.astimezone()
        horizon_end = now_a + timedelta(hours=_PLAN_HORIZON_H)

        batt_min_kwh = battery_capacity_kwh * self.battery_min_soc / 100.0
        batt_max_kwh = battery_capacity_kwh * self.battery_max_soc / 100.0
        batt_kwh     = battery_capacity_kwh * battery_soc_pct / 100.0
        # Samma mönster som _auto_mode() (v0.7.6): predicted_daily_kwh
        # (temperaturmodell) täcker bara uppvärmning/varmvatten, inte den
        # generella hushållsbaslasten – ta max mot verklig förbrukning.
        # Föredrar det 7-dygns rullande snittet (netto exkl. extra
        # varmvatten-energi, se coordinator._get_rolling_consumption_kwh())
        # framför en enda dags gårdagssiffra – en enstaka ovanligt hög dag
        # (elpatron, EV, varmvatten) ska inte ensam blåsa upp golvet för hela
        # natten (docs/forbrukningsanalys.md avsnitt 8: gårdagens 28,76 kWh
        # låg ~20% över 17-dagarssnittet och drev golvet mot taket på egen
        # hand). Faller tillbaka till gårdagen ensam tills 7 dygn hunnit
        # rulla över sedan funktionen driftsattes. Projektionen använder
        # house_load_avg_w (glidande medel) om den finns, annars momentan
        # house_load_w – en enstaka kokplatta/dusch ska inte läsas som
        # "hela natten".
        _eff_daily_kwh = max(predicted_daily_kwh, rolling_consumption_kwh or yesterday_consumption_kwh or 0.0)
        _load_for_projection_w = house_load_avg_w if house_load_avg_w is not None else house_load_w
        hourly_load_kw = max(_eff_daily_kwh / 24.0, _load_for_projection_w / 1000.0, 0.5)

        # v1.0 steg 1A: lastens FORM (24 timhinkar, rullande <=21-dygnssnitt,
        # normaliserat per dygn – se coordinator._get_load_shape()) skild från
        # NIVÅN (gradtimmodellen, predicted_daily_kwh – INTE _eff_daily_kwh,
        # eftersom formen redan är byggd på verklig data och nivån ska kunna
        # reagera på morgondagens temperaturprognos direkt). P50 för allmän
        # planering, P75 för reserven (behov_kwh/net_solar_tomorrow_kwh) – en
        # smalt högre försiktighetsmarginal, "augustinattens premie" är bara
        # 1–10 % över P50. Faller tillbaka till den platta hourly_load_kw tills
        # 21 dygns formhistorik hunnit byggas upp, eller för enskilda timmar
        # utan täckning i fönstret.
        def _load_kw_at(dt: datetime, shape: Optional[list]) -> float:
            if shape is not None:
                h = shape[dt.astimezone().hour]
                if h is not None:
                    return predicted_daily_kwh * h
            return hourly_load_kw

        def _slot_load_kwh(s, shape: Optional[list]) -> float:
            return _load_kw_at(s.start, shape) * (s.end - s.start).total_seconds() / 3600.0

        # v1.0 steg 2: golvet blir en BANA, inte ett skalärt tal (docs/
        # v1_implementation_plan.md). Den gamla versionen räknade en enda
        # export_floor_kwh en gång och jämförde den mot VARJE mörk slot genom
        # hela natten – men golvet krympte aldrig i takt med att lasten
        # faktiskt täcktes, så "avail = batt − min − golv" kunde bli 0 långt
        # innan solen tog över, trots att batteriet redan hade täckt det
        # golvet var till för (se docs/forbrukningsanalys.md avsnitt 9, Steg 0
        # – executorns veto var SYMPTOMET, det här var grundorsaken det lappade).
        #
        # reserve_at(t) = summan av (last(s) − sol_p10(s)) för alla floor_slots
        # med start >= t, plus en icke-avtagande säkerhetsmarginal (+2 kWh,
        # EV-marginal), × osäkerhetspåslaget. Eftersom både batteriet OCH
        # reservkravet minskar med samma kWh när en slots last täcks blir
        # "avail" positivt genom hela natten – det är hela poängen.
        #
        # Osäkerhetspåslaget skalas av produktionskvoten (P3-2): slår
        # prognosen fel (snö, nedsmutsning) höjs reserven, i värsta fall mot
        # full nattautonomi via min()-klämningen mot batt_max_kwh. Ett hårt
        # golv (10 % av kapaciteten) gäller alltid.
        takeover = solar_takeover_dt if (solar_takeover_dt and solar_takeover_dt > now_a) else now_a + timedelta(hours=9)
        takeover_local = takeover.astimezone()
        _floor_slots = [s for s in (ps.slots or []) if s.end > now_a and s.start < takeover_local]
        _floor_slots_sorted = sorted(_floor_slots, key=lambda s: s.start)

        # Suffix-summa (rå, ingen markup/buffert än): för varje floor_slot,
        # hur mycket underskott återstår FRÅN den sloten till takeover.
        _reserve_suffix: dict[datetime, float] = {}
        _running_raw = 0.0
        for s in reversed(_floor_slots_sorted):
            _running_raw += max(0.0, _slot_load_kwh(s, load_shape_p75) - s.solar_kwh_p10)
            _reserve_suffix[s.start] = _running_raw

        # EV-marginal + fast buffert: en icke-avtagande säkerhetsmarginal
        # (inte hela vägen till soc_target för EV – EV-laddning är
        # fortfarande i första hand sol-/opportunistiskt styrd), så en vald
        # bil som kan behöva ladda under det mörka fönstret aldrig glöms bort,
        # oavsett var i natten vi är.
        _flat_buffer_kwh = 2.0 + max(0.0, ev_reserve_margin_kwh)
        _markup_factor = 1.0 + _uncertainty_markup(pv_production_ratio)
        hard_floor_kwh = battery_capacity_kwh * _HARD_FLOOR_FRACTION
        # Skyddsspärren (_FLOOR_SAFETY_CAP_FRACTION) tas bort här (v1.0 steg 2,
        # punkt 2): den var en lapp mot att det SKALÄRA golvet kunde överstiga
        # hela det användbara spannet en högförbrukningsdag. En bana som
        # summerar riktiga per-slot-underskott mot takeover kan strukturellt
        # inte göra det – klämningen mot batt_max_kwh nedan räcker.

        def reserve_at(t: datetime) -> float:
            """Reservkrav (kWh, med markup+buffert, klämt mot hård gräns) för
            allt som återstår från t till takeover. Krymper mot bara
            bufferten när t närmar sig takeover."""
            if t >= takeover_local:
                raw = 0.0
            elif _floor_slots_sorted:
                raw = 0.0
                for s in _floor_slots_sorted:
                    if s.start >= t:
                        raw = _reserve_suffix[s.start]
                        break
            else:
                # Inga riktiga prisslots att summera över (t.ex. saknad
                # Nordpool-data) – uppskatta mot återstående timmar till
                # takeover istället för en fast siffra, så även fallbacken
                # krymper naturligt när t närmar sig takeover.
                _remaining_h = max(0.0, (takeover_local - t).total_seconds() / 3600.0)
                raw = _load_kw_at(t, load_shape_p75) * _remaining_h
            reserve = (raw + _flat_buffer_kwh) * _markup_factor
            return min(batt_max_kwh, max(hard_floor_kwh, reserve))

        # Golvet vid "nu" – samma tal som tidigare (export_floor_kwh),
        # bevarat för rapportering (DayPlan, sensorer, kvällsmål) och som
        # startvärde för export-/nätladdningsbesluten som bara utvärderas en
        # gång per planeringscykel, inte per slot.
        export_floor_kwh = reserve_at(now_a)
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
                max(0.0, s.solar_kwh_p10 - _slot_load_kwh(s, load_shape_p75))
                for s in tomorrow_slots
            ) if tomorrow_slots
            else max(0.0, solar_forecast_tomorrow_kwh - _load_kw_at(now_a, load_shape_p75) * 13.0)
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
            and (s.solar_kw < _load_kw_at(s.start, load_shape_p50) if has_solar_data
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

        # Koncentrerad dispatch av exporterbara kWh: dela high_slots i "ikväll"
        # och "imorgon bitti" (kalenderdag), räkna kWh-viktat snittpris per
        # grupp, och allokera exportable_kwh till den HÖGST värderade gruppen
        # först – upp till vad gruppens egna slots kan bära (effekt × tid).
        # Bara om den gruppen inte kan bära allt spiller resten över till den
        # andra. Ersätter den tidigare rena linjära prisviktningen över ALLA
        # high_slots på en gång, som smetade ut exporten proportionellt även
        # när en av topparna var betydligt mer värd än den andra (t.ex.
        # imorgon-morgontoppen 155,7 öre mot kvällens 127,3 öre).
        export_plan: dict[datetime, float] = {}
        can_export = (
            exportable_kwh > 0.1
            and net_solar_tomorrow_kwh >= export_floor_kwh
            and solar_forecast_tomorrow_kwh >= self.export_min_solar_tomorrow_kwh
        )
        export_plan_price_sum: dict[datetime, float] = {}
        if can_export and high_slots:
            groups: dict[object, list] = {}
            for s in high_slots:
                groups.setdefault(s.start.astimezone().date(), []).append(s)

            def _group_value(group_slots: list) -> float:
                total_h = sum((s.end - s.start).total_seconds() / 3600.0 for s in group_slots)
                if total_h <= 0:
                    return 0.0
                return sum(s.sell_sek * (s.end - s.start).total_seconds() / 3600.0 for s in group_slots) / total_h

            def _group_capacity_kwh(group_slots: list) -> float:
                return sum(
                    battery_max_power_kw * (s.end - s.start).total_seconds() / 3600.0
                    for s in group_slots
                )

            ordered_groups = sorted(groups.values(), key=_group_value, reverse=True)
            remaining_kwh = exportable_kwh
            for group_slots in ordered_groups:
                if remaining_kwh <= 0.01:
                    break
                group_kwh = min(remaining_kwh, _group_capacity_kwh(group_slots))
                group_price_sum = sum(s.sell_sek for s in group_slots)
                if group_kwh <= 0 or group_price_sum <= 0:
                    continue
                for s in group_slots:
                    slot_h = (s.end - s.start).total_seconds() / 3600.0
                    if slot_h <= 0:
                        continue
                    w = (s.sell_sek / group_price_sum) * group_kwh / slot_h * 1000.0
                    export_plan[s.start] = min(w, battery_max_power_kw * 1000.0)
                    export_plan_price_sum[s.start] = group_price_sum
                remaining_kwh -= group_kwh

        # Billiga nätladdningssots: bland mörka slots, lägsta 25%
        dark_slots = [s for s in future_slots if s.solar_kw < _load_kw_at(s.start, load_shape_p50) and s.start not in export_plan]
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
            # "Mörk" = solen täcker inte huslasten, inte ett godtyckligt kW-tak
            # (v1.0 steg 0) – en fast 2 kW-gräns klassade stora delar av
            # mellansäsongens och vinterns dagsljus som "natt". Lastnivån per
            # slot kommer nu från form×nivå (v1.0 steg 1A) när formhistorik
            # finns, annars den platta hourly_load_kw.
            _slot_load_kw = _load_kw_at(slot.start, load_shape_p50)
            is_dark = slot.solar_kw < _slot_load_kw
            solar_kwh = slot.solar_kw * slot_h
            load_kwh  = _slot_load_kw * slot_h

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
                _grp_sum = export_plan_price_sum.get(slot.start, slot.sell_sek)
                reason  = f"sälj {slot.sell_sek:.2f} kr/kWh vikt {slot.sell_sek:.2f}/{_grp_sum:.2f}"

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
                    # Golvet är reserve_at(slot.end) (v1.0 steg 2): kravet EFTER
                    # den här sloten, inte ett fast dygnstal – annars räknas
                    # samma reserv en gång per slot hela dagen ut.
                    deficit_kwh = load_kwh - solar_kwh
                    avail_kwh = max(0.0, batt_kwh - batt_min_kwh - reserve_at(slot.end))
                    if avail_kwh > 0.01:
                        dis_kwh = min(deficit_kwh, avail_kwh, battery_max_power_kw * slot_h)
                        batt_kwh -= dis_kwh
                        power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                        action = "cover_load"
                        reason = f"sol {slot.solar_kw:.1f}kW < last {_slot_load_kw:.2f}kW → batteri {-power_w:.0f}W"
                    else:
                        action = "cover_load"
                        reason = f"sol {slot.solar_kw:.1f}kW < last {_slot_load_kw:.2f}kW → batteri vid golvet, nät"

            elif slot.start in cheap_set and batt_kwh < reserve_at(slot.start) - 0.5:
                _target_kwh = reserve_at(slot.start)
                needed = min(_target_kwh - batt_kwh, battery_max_power_kw * slot_h)
                batt_kwh = min(batt_max_kwh, batt_kwh + needed)
                power_w = min(needed / slot_h * 1000.0, battery_max_power_kw * 1000.0) if slot_h > 0 else 0.0
                action = "grid_charge"
                reason = f"nätladda {slot.buy_sek:.2f} kr/kWh (gräns {cheap_threshold:.2f}, mål {_target_kwh:.1f}kWh)"

            else:
                # Mörk slot utan export/nätladdning – självkonsumtion om batteri > golvet.
                # Golvet är reserve_at(slot.end) (v1.0 steg 2) – en bana som redan
                # drar av sol_p10 per slot i floor_slots-summan, så den gamla
                # 80%-av-kommande-2h-solen-hacken (_eff_floor) behövs inte längre;
                # den dubbelräknade samma sol som reserven redan tagit hänsyn till.
                deficit_kwh = max(0.0, load_kwh - solar_kwh)
                avail_kwh = max(0.0, batt_kwh - batt_min_kwh - reserve_at(slot.end))
                # Interimslösning ("Option B", se konstant-kommentaren ovan): vid
                # golvet men köppriset överstiger vad energin i batteriet redan
                # kostat (+ cykelkostnad) – öppna ner mot hard_floor istället för
                # att köpa nät som är dyrare än batteriets egen sparade energi.
                # Osäkerheten i solprognosen är INTE ett villkor här (v1.0 steg 0)
                # – den hanteras redan av _uncertainty_markup() i golvet självt.
                # Behålls tills reserve_at-banan är verifierad i backtest (v1.0
                # plan, steg 2) – tas bort då, inte innan.
                price_gate_used = False
                if avail_kwh <= 0.01 and deficit_kwh > 0.01:
                    price_threshold = battery_avg_cost_sek_kwh + self.cycle_cost_sek_kwh
                    if slot.buy_sek > price_threshold:
                        # hard_floor ÄR det absoluta golvet – ersätter batt_min_kwh
                        # här, adderas inte till det (annars låser ett golv och en
                        # min_soc på samma nivå, t.ex. 10%+10%, en spärr på 20%).
                        avail_kwh_gated = max(0.0, batt_kwh - hard_floor_kwh)
                        if avail_kwh_gated > 0.01:
                            avail_kwh = avail_kwh_gated
                            price_gate_used = True
                if avail_kwh > 0.01 and deficit_kwh > 0.01:
                    dis_kwh = min(deficit_kwh, avail_kwh, battery_max_power_kw * slot_h)
                    batt_kwh -= dis_kwh
                    power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                    action = "cover_load"
                    if price_gate_used:
                        reason = f"mörk: prisspärr under golvet {-power_w:.0f}W (köp {slot.buy_sek:.2f} > batteri {battery_avg_cost_sek_kwh + self.cycle_cost_sek_kwh:.2f}) batteri kvar {batt_kwh:.1f}kWh"
                    else:
                        reason = f"mörk: självkonsumtion {-power_w:.0f}W batteri kvar {batt_kwh:.1f}kWh"
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
