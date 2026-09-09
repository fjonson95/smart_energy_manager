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


def _uncertainty_markup(pv_production_ratio: float) -> float:
    """Riskpåslag på V baserat på produktionskvoten (P3-2).

    Kvot ~1.0 (normal produktion mot prognos) → litet baspåslag.
    Kvot ≤0.3 (snötäckta paneler etc.) → stort påslag – ett högre V ger
    mindre export, sparsammare självkonsumtion och nätladdning vid högre
    priser, alla tre effekterna man vill ha när solprognosen inte går att
    lita på (v1.0 steg 3, punkt 3 – flyttad hit från golvbanan i steg 2).
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
    ev_charge_w: float = 0.0    # v1.0 steg 6: schemalagd EV-laddeffekt denna slot, 0 = ingen


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
        # v1.0 steg 3: export_sell_percentile, export_min_sell_price,
        # export_min_solar_tomorrow_kwh, sell_solar_min_price och
        # cheap_charge_buy_percentile används inte längre av build_plan() –
        # de nio konkurrerande trösklarna är ersatta av V och de fyra
        # beslutsreglerna (_decide_slot). Kvar i konstruktorn bara för att
        # coordinator.py fortfarande skickar in dem – tas bort i steg 4
        # tillsammans med motsvarande CONF_*-nycklar (docs/v1_implementation_plan.md).
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
        ev_energy_needed_kwh: float = 0.0,
        ev_deadline: Optional[datetime] = None,
        ev_max_power_kw: float = 0.0,
    ) -> DayPlan:
        # solar_forecast_tomorrow_kwh och solar_takeover_dt konsumeras inte
        # längre av logiken nedan (v1.0 steg 3): V:s merit-order-allokering
        # ser redan morgondagens sol_p10 per slot via ps.slots. Kvar i
        # signaturen bara för att inte behöva röra coordinator.py:s anrop
        # innan steg 4:s städning. battery_avg_cost_sek_kwh används
        # däremot fortfarande, som en broms mot regel 4 (export) – se
        # kommentaren där.
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
        # natten (docs/forbrukningsanalys.md avsnitt 8). Faller tillbaka till
        # gårdagen ensam tills 7 dygn hunnit rulla över sedan funktionen
        # driftsattes. Projektionen använder house_load_avg_w (glidande
        # medel) om den finns, annars momentan house_load_w – en enstaka
        # kokplatta/dusch ska inte läsas som "hela natten".
        _eff_daily_kwh = max(predicted_daily_kwh, rolling_consumption_kwh or yesterday_consumption_kwh or 0.0)
        _load_for_projection_w = house_load_avg_w if house_load_avg_w is not None else house_load_w
        hourly_load_kw = max(_eff_daily_kwh / 24.0, _load_for_projection_w / 1000.0, 0.5)

        # v1.0 steg 1A: lastens FORM (24 timhinkar, rullande <=21-dygnssnitt,
        # normaliserat per dygn – se coordinator._get_load_shape()) skild från
        # NIVÅN (gradtimmodellen, predicted_daily_kwh – INTE _eff_daily_kwh,
        # eftersom formen redan är byggd på verklig data och nivån ska kunna
        # reagera på morgondagens temperaturprognos direkt). P50 för allmän
        # planering, P75 för underskottsprojektionen i V (en smalt högre
        # försiktighetsmarginal). Faller tillbaka till den platta
        # hourly_load_kw tills 21 dygns formhistorik hunnit byggas upp, eller
        # för enskilda timmar utan täckning i fönstret.
        def _load_kw_at(dt: datetime, shape: Optional[list]) -> float:
            if shape is not None:
                h = shape[dt.astimezone().hour]
                if h is not None:
                    return predicted_daily_kwh * h
            return hourly_load_kw

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

        # --- v1.0 steg 3: marginalvärdet V --------------------------------
        # "Vattenvärdet" av en kWh i batteriet just nu: allokera den
        # tillgängliga batterienergin (ovan hård gräns + buffert) mot en
        # rankad lista av framtida "möjligheter" (täcka ett underskott ELLER
        # exportera, se _opportunities nedan) i värdeordning, dyrast först,
        # begränsat av effekt per slot OCH av vad solen hinner fylla på
        # innan respektive slots tidpunkt (inte en delad pool som kan tas i
        # fel kronologisk ordning). V = värdet (köp- eller säljpris) i den
        # billigaste möjlighet som fick tilldelning.
        #
        # _cap_by_time: kronologisk, prisfri simulering av hur mycket
        # batteriet SOM MEST kan innehålla vid varje framtida tidpunkt om
        # det bara någonsin laddas av sol (aldrig urladdas) – den övre
        # gränsen "vad solen fyller på däremellan" sätter för en slot sent i
        # horisonten. Använder PESSIMISTISK sol (p10) och P75-last, samma
        # försiktighetskonvention som resten av reservlogiken – inte
        # förväntad (p50) sol. Ett optimistiskt antagande här skapar en
        # cirkularitet: om capacity-prognosen antar att all sol fångas ser
        # kvällens underskott lätt-täckt ut redan mitt på dagen, V faller,
        # regel 1 säljer solen direkt istället för att spara den – vilket
        # gör antagandet falskt i efterhand. Verifierat i backtest: gav en
        # tydlig regression (53% besparing mot steg 2:s 91%, mitt-på-dagen-
        # sol som skulle laddat batteriet för natten exporterades istället
        # för nästan inget). Se docs/v1_implementation_plan.md, Steg 3
        # implementerat.
        _cap_by_time: dict[datetime, float] = {}
        _running_cap = batt_kwh
        for s in future_slots:
            s_h = (s.end - s.start).total_seconds() / 3600.0
            if s_h <= 0:
                continue
            _s_surplus_kwh = max(0.0, s.solar_kwh_p10 - _slot_load_kwh(s, load_shape_p75))
            _running_cap = min(batt_max_kwh, _running_cap + min(_s_surplus_kwh, battery_max_power_kw * s_h))
            _cap_by_time[s.start] = _running_cap

        _reserved_kwh = batt_min_kwh + _flat_buffer_kwh

        # Merit-order över ALLA framtida "möjligheter" en kWh batteri kan
        # användas till – inte två separata pass (köpsida/säljsida) som
        # visade sig kunna falla mellan stolarna och sätta V=0.0 (t.ex. när
        # batteriet exakt räckte till alla underskott utan någon marginal
        # kvar för säljsidans egen allokering, eller när batteriet redan låg
        # UNDER den bevarade reserven vid horisontens start så ingenting
        # kunde tilldelas alls – båda upptäckta i backtest, se
        # docs/v1_implementation_plan.md, Steg 3 implementerat).
        #
        # Varje slot bidrar med upp till två "möjligheter" efter varandra,
        # ur samma effektbudget: först att täcka slotens eget underskott
        # (värde = köppris, det den sparar), sedan – med vad som blir över
        # av effekten – att exportera (värde = säljpris, det den tjänar).
        # Rankas dyrast/mest värdefullt först, vilket automatiskt prioriterar
        # lasttäckning före export när båda konkurrerar om samma kWh (köp>sälj
        # alltid – matchar CLAUDE.md:s prioritet 2 före 3 utan specialfall).
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

        # Kronologisk marginal-tracker: en kandidat vid t_i tas bara emot om
        # den ryms UNDER TAKET VID VARJE framtida tidpunkt >= t_i, inte bara
        # vid t_i självt. En tidigare version jämförde bara mot summan av
        # REDAN accepterade möjligheter med start < t_i – men genomgången
        # sker i VÄRDEORDNING, inte kronologisk ordning, så en senare (i
        # tiden) hög-värderad möjlighet kunde accepteras FÖRE en tidigare
        # lågvärderad möjlighet ens prövats, utan att reservera utrymme åt
        # den – battericap räknades då flera gånger om. Verifierat i
        # backtest: total accepterad volym låg på 62–70 kWh mot fysiskt
        # tillgängliga ~22 kWh (batt_max−reserverat) innan den här fixen.
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

        V = _v_raw * (1.0 + _uncertainty_markup(pv_production_ratio))
        # Regel 1 och 3 LADDAR ny energi in i batteriet – bara
        # eta_roundtrip-andelen av det överlever till att kunna användas/
        # säljas senare, så tröskeln för att spara/nätladda måste vara
        # strängare än för att ta UT redan lagrad energi (regel 2/4, ingen
        # ytterligare förlust där, den skedde redan vid laddningstillfället).
        # Utan den här faktorn (första implementationsförsöket) blev
        # regel 3+4 tillsammans en nästan fri arbitrage-loop – 12 öres
        # marginal räckte för att motivera en hel laddcykel trots ~15 %
        # rundgångsförlust, vilket i backtest gav SÄMRE resultat än steg 2
        # (62 % besparing mot 91 %) istället för bättre. Se
        # docs/v1_implementation_plan.md, Steg 3 implementerat.
        V_charge = V * self.eta_roundtrip

        evening_target_soc = min(self.battery_max_soc, self.battery_min_soc + _flat_buffer_kwh / battery_capacity_kwh * 100.0)
        exportable_kwh = max(0.0, batt_kwh - _reserved_kwh)

        # v1.0 steg 5, punkt 1+3: merit-order på säljsidan för dagens
        # solöverskott. Regel 1:s huvudloop nedan går igenom future_slots
        # KRONOLOGISKT och fyller batteriet giriga – så länge V_charge>sälj
        # och rum finns. Det kan fylla från en förmiddagsslot med
        # medelmåttigt säljpris och sedan sakna rum för en billigare (mer
        # värd att spara, t.ex. mitt på dagen vid solöverskott) slot som
        # råkar komma senare kronologiskt. Förbered istället en
        # prioritetsordning: rangordna alla överskottsslots som klarar
        # V-tröskeln efter säljpris STIGANDE (billigast/mest värt att spara
        # först) och dela ut det tillgängliga rummet (batt_max − nuvarande
        # batt_kwh) i den ordningen. Huvudloopen använder sedan
        # min(verkligt rum just då, denna prioritetsandel) som tak – aldrig
        # mer generöst än endera, men respekterar rangordningen när rummet
        # är den bindande begränsningen. Samma mekanism ger effektivt även
        # punkt 3 ("morgontoppen före kvällstoppen") gratis: en dyrare
        # förmiddagsslot som konkurrerar om samma rum med en billigare
        # eftermiddagsslot förlorar prioritetsordningen, exporteras direkt
        # istället – vilket lämnar rum kvar åt den billigare eftermiddags-
        # solen att fylla batteriet med.
        # Rummet delas bara mellan slots i SAMMA sammanhängande dagsljus-
        # fönster – inte hela 48h-horisonten. Annars kan en billigare slot
        # imorgon felaktigt reservera bort rum från en dyrare men ändå
        # värd-att-spara slot idag, trots att natten emellan (regel 2:s
        # egenförbrukning) redan hinner frigöra nytt rum. Kandidatlistan
        # avbryts vid första genuina mörka underskottsslot efter nu.
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
                    if avail > 0.01:
                        dis_kwh = min(deficit_kwh, avail, battery_max_power_kw * slot_h)
                        batt_kwh -= dis_kwh
                        power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                        action = "cover_load"
                        reason = f"köp {slot.buy_sek:.2f}>V {V:.2f}+cykel → batteri {-power_w:.0f}W"
                    else:
                        action = "cover_load"
                        reason = f"köp {slot.buy_sek:.2f}>V {V:.2f}+cykel men batteri vid reserven → nät"
                else:
                    reason = f"köp {slot.buy_sek:.2f}≤V {V:.2f}+cykel → nät billigare, spara batteriet"
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
                if slot.buy_sek + self.cycle_cost_sek_kwh < V_charge and room_kwh > 0.1:
                    charge_kwh = min(room_kwh, battery_max_power_kw * slot_h)
                    batt_kwh = min(batt_max_kwh, batt_kwh + charge_kwh)
                    power_w = min(charge_kwh / slot_h * 1000.0, battery_max_power_kw * 1000.0) if slot_h > 0 else 0.0
                    action = "grid_charge"
                    reason = f"nätladda {slot.buy_sek:.2f}+cykel<V·η {V_charge:.2f}"
                # Regel 4:s tröskel är max(V, battery_avg_cost) + cykel, inte
                # bara V. V beräknas om varje planeringscykel mot en 48h-
                # horisont som vandrar framåt i tiden – en affär som var
                # lönsam när energin laddades (V högt då) kan se lönsam ut
                # för export senare även om V sjunkit under tiden, eftersom
                # V bara jämförs mot NUET, inte mot vad energin faktiskt
                # kostade. Verifierat i backtest: grid_charge-snittpriset
                # (1,47 kr/kWh) låg FAKTISKT ÖVER export-snittpriset
                # (1,43 kr/kWh) innan den här spärren – handeln var i
                # praktiken ett nollsummespel trots att varje enskilt beslut
                # följde V korrekt vid sitt eget tillfälle. battery_avg_cost
                # (bokförd kostnad, se sensor.py:s ackumulator) är den enda
                # tillgängliga bromsen mot just den glidande-horisont-effekten.
                elif deficit_kwh <= 0.01 and slot.sell_sek > max(V, battery_avg_cost_sek_kwh) + self.cycle_cost_sek_kwh:
                    avail = max(0.0, batt_kwh - _reserved_kwh)
                    if avail > 0.01:
                        dis_kwh = min(avail, battery_max_power_kw * slot_h)
                        batt_kwh -= dis_kwh
                        power_w = -(dis_kwh / slot_h * 1000.0) if slot_h > 0 else 0.0
                        action = "export"
                        expected_revenue += dis_kwh * slot.sell_sek
                        reason = f"exportera {slot.sell_sek:.2f}>max(V {V:.2f}, snittkostnad {battery_avg_cost_sek_kwh:.2f})+cykel"

            planned.append(PlannedSlot(
                start=slot.start.astimezone(),
                end=slot.end.astimezone(),
                action=action,
                target_power_w=power_w,
                battery_soc_est_pct=round(soc_est, 1),
                reason=reason,
            ))

        # v1.0 steg 6, punkt 1: bilen som schemalagd last. Ett fristående
        # merit-order-schema (INTE kopplat till batteriets V eller
        # _opportunities – EV-energin drar inte på batteriets kapacitet,
        # det är en oberoende nät-/soldragning med sin egen deadline):
        # rangordna slots fram till deadline efter köppris STIGANDE, fyll
        # behovet från de billigaste först, begränsat av bilens maxeffekt.
        #
        # Punkt 2 (fas-1-samordning) hanteras här bara som en enkel, säker
        # regel: undvik att lägga EV-laddning i samma slot som batteriets
        # egen grid_charge (den enda batteriåtgärden som aktivt drar NY
        # nätkraft samtidigt – 8 kW batteri + 3,7 kW bil är exakt den
        # kombination Steg 6 själv varnar för). Ingen fullständig per-fas-
        # modell finns ännu i planeraren (bara en skalär batterieffekt, ingen
        # fasuppdelning) – det kräver mer indata (max_current_per_phase,
        # spänning, bilens effektiva faser) än vad build_plan() tar emot
        # idag. Se docs/v1_implementation_plan.md, Steg 6 implementerat.
        #
        # Punkt 3 (invarianten "bilen laddas bara ur solöverskott eller
        # uttryckligt schemalagda slots") är en EXEKVERINGSregel, inte en
        # planeringsregel – kräver att coordinator.py/energy_controller.py
        # faktiskt läser och respekterar det här schemat, vilket INTE görs
        # här (se stoppet inför att röra levande styrlogik, samma skäl som
        # steg 4 pausades).
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
            f"satt av {_marginal_slot_start.astimezone().strftime('%d %H:%M') if _marginal_slot_start else 'bästa säljpris'} | "
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
            marginal_slot_start=_marginal_slot_start,
            slots=planned,
            notes=notes,
        )
