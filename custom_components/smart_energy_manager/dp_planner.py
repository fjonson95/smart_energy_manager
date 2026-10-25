"""Batteriplan med dynamisk programmering över 48 h (fas B, HANDOFF.md 6B).

Löser hela horisonten på en gång: för varje kvart och lagrad energinivå väljs
den batterieffekt som minimerar nätkostnaden från och med nu, givet kända
priser, Solcast p50 och lastprognosen. Ersätter V-heuristiken (regel 1–4,
merit-order, öronmärkning), vars köp- och urladdningsbeslut inte löstes
tillsammans (HANDOFF.md 4.1–4.4).

Samma anrop och samma DayPlan som EnergyPlanner, så exekutor, sensorer, kort
och planlogg fungerar oförändrade. Ren logik utan HA-beroenden.

Batterimodell (docs/decisions.md B1): η delas lika på laddning/urladdning,
konstant maxeffekt inom min–max SOC, cykelkostnad per urladdad kWh. Energin
diskretiseras i _STEP_KWH; en åtgärd flyttar ett helt antal steg per kvart.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np

from .const import DP_LOAD_BALANCE_TEMP_C, DP_LOAD_K_KWH_DD
from .energy_planner import DayPlan, PlannedSlot, _PLAN_HORIZON_H

_STEP_KWH = 0.1


def calibrated_daily_levels(
    history: list[dict],
    today: date,
    forecast_mean_temp_by_date: Optional[dict] = None,
    days_ahead: int = 2,
) -> Optional[dict]:
    """Dygnsnivå (kWh) för idag och days_ahead dygn framåt.

    history: avslutade dygn, äldst först, med net_kwh och (valfritt)
    mean_temp_c. Nivå = snittet av de senaste 7 dygnens net_kwh +
    k × (graddagar för dygnet − snittgraddagar för de dygn som har
    temperatur). Dygnets temperatur tas ur prognosen, annars senast kända
    dygnsmedel (persistens – i backtesten lika bra som perfekt temperatur).
    """
    recent = [h for h in history[-7:] if h.get("net_kwh") is not None]
    if not recent:
        return None
    base = sum(float(h["net_kwh"]) for h in recent) / len(recent)
    temps = [float(h["mean_temp_c"]) for h in recent if h.get("mean_temp_c") is not None]

    def dd(temp_c: float) -> float:
        return max(0.0, DP_LOAD_BALANCE_TEMP_C - temp_c)

    forecast = forecast_mean_temp_by_date or {}
    out = {}
    for offset in range(days_ahead + 1):
        d = today + timedelta(days=offset)
        if not temps:
            out[d] = base
            continue
        dd_mean = sum(dd(t) for t in temps) / len(temps)
        temp_c = forecast.get(d, temps[-1])
        out[d] = max(0.0, base + DP_LOAD_K_KWH_DD * (dd(temp_c) - dd_mean))
    return out
# Prisskillnader under detta (kr/kWh) räknas som lika – annars väljer DP:n
# slumpmässiga små laddningar/urladdningar mellan nästan identiska kvartar.
_TIE_EPS = 1e-6


class DPPlanner:
    def __init__(
        self,
        battery_min_soc: float = 10.0,
        battery_max_soc: float = 95.0,
        eta_roundtrip: float = 0.87,
        cycle_cost_sek_kwh: float = 0.10,
        reserve_margin_kwh: float = 0.0,
        terminal_mode: str = "mean24",
    ):
        """Standardvärden beslutade i B3 (docs/decisions.md): reserv 0 kWh över
        min-SOC (Sonnen stannar själv vid 8 %), cykelkostnad 0,10 kr/kWh.

        terminal_mode: "mean24" = kvarvarande energi till snittköp sista
        24 h; "repeat" = sista 24 h:s priser, sol och last upprepas ett dygn
        till innan snittvärdet sätts (syntetiskt tredje dygn)."""
        self.battery_min_soc = battery_min_soc
        self.battery_max_soc = battery_max_soc
        self.eta_roundtrip = eta_roundtrip
        self.cycle_cost_sek_kwh = cycle_cost_sek_kwh
        self.reserve_margin_kwh = reserve_margin_kwh
        self.terminal_mode = terminal_mode

    def build_plan(
        self,
        now: datetime,
        battery_soc_pct: float,
        battery_capacity_kwh: float,
        battery_max_power_kw: float,
        ps,
        predicted_daily_kwh: float,
        solar_forecast_tomorrow_kwh: float = 0.0,
        solar_takeover_dt: Optional[datetime] = None,
        house_load_w: float = 0.0,
        pv_production_ratio: float = 1.0,
        yesterday_consumption_kwh: Optional[float] = None,
        house_load_avg_w: Optional[float] = None,
        ev_reserve_margin_kwh: float = 0.0,
        rolling_consumption_kwh: Optional[float] = None,
        load_shape_p50: Optional[list] = None,
        daily_load_kwh_by_date: Optional[dict] = None,
        load_kw_by_start: Optional[dict] = None,
        **_unused,
    ) -> DayPlan:
        """load_kw_by_start: {slot-start: kW} som ersätter lastmodellen –
        bara för backtestens orakelkörning (perfekt lastprognos)."""
        now_a = now if now.tzinfo else now.astimezone()
        horizon_end = now_a + timedelta(hours=_PLAN_HORIZON_H)
        cap = battery_capacity_kwh
        e_min = cap * self.battery_min_soc / 100.0
        e_max = cap * self.battery_max_soc / 100.0
        e_now = min(max(cap * battery_soc_pct / 100.0, e_min), e_max)
        reserve_kwh = min(e_max, e_min + self.reserve_margin_kwh + max(0.0, ev_reserve_margin_kwh))

        floor_kwh = rolling_consumption_kwh or yesterday_consumption_kwh or 0.0
        eff_daily = max(predicted_daily_kwh, floor_kwh)
        projection_w = house_load_avg_w if house_load_avg_w is not None else house_load_w
        hourly_load_kw = max(eff_daily / 24.0, projection_w / 1000.0, 0.5)

        def level_on(dt: datetime) -> float:
            if daily_load_kwh_by_date:
                # Nivån per dygn är redan en kalibrerad prognos (7 dygns snitt +
                # temperaturdelta, docs/decisions.md B3); golvet max(gårdag, 7 d)
                # låg i snitt 3 kWh/dygn för högt och läggs inte ovanpå.
                level = daily_load_kwh_by_date.get(dt.astimezone().date())
                if level is not None:
                    return level
            return eff_daily

        def load_kw_at(dt: datetime) -> float:
            if load_kw_by_start is not None:
                key = dt.replace(minute=dt.minute - dt.minute % 15, second=0, microsecond=0)
                if key in load_kw_by_start:
                    return load_kw_by_start[key]
            if load_shape_p50 is not None:
                frac = load_shape_p50[dt.astimezone().hour]
                if frac is not None:
                    return level_on(dt) * frac
            return max(level_on(dt) / 24.0, hourly_load_kw)

        slots = [s for s in (ps.slots if ps else []) if s.end > now_a and s.start < horizon_end]
        if not slots:
            return DayPlan(
                generated_at=now_a, valid_until=now_a + timedelta(minutes=15),
                evening_target_soc_pct=reserve_kwh / cap * 100.0, export_floor_kwh=reserve_kwh,
                total_exportable_kwh=max(0.0, e_now - reserve_kwh), expected_revenue_sek=0.0,
                solar_takeover_dt=solar_takeover_dt, hourly_load_kw=hourly_load_kw,
                pv_production_ratio=pv_production_ratio, notes="DP: inga prisslots tillgängliga",
            )

        n = len(slots)
        hours = np.empty(n)
        net = np.empty(n)    # last − sol (kWh), positiv = underskott
        buy = np.empty(n)
        sell = np.empty(n)
        for t, s in enumerate(slots):
            start = max(s.start, now_a)
            h = max(1e-6, (s.end - start).total_seconds() / 3600.0)
            frac = h / max(1e-6, (s.end - s.start).total_seconds() / 3600.0)
            hours[t] = h
            net[t] = load_kw_at(start) * h - s.solar_kwh * frac
            buy[t] = s.buy_sek
            sell[t] = s.sell_sek

        n_real = n
        if self.terminal_mode == "repeat":
            tail_idx = [t for t, s in enumerate(slots) if s.start >= slots[-1].end - timedelta(hours=24)]
            hours = np.concatenate([hours, hours[tail_idx]])
            net = np.concatenate([net, net[tail_idx]])
            buy = np.concatenate([buy, buy[tail_idx]])
            sell = np.concatenate([sell, sell[tail_idx]])
            n = len(hours)

        eta_leg = math.sqrt(self.eta_roundtrip)
        n_states = int(round((e_max - e_min) / _STEP_KWH)) + 1
        energies = e_min + np.arange(n_states) * _STEP_KWH
        i_reserve = int(math.ceil((reserve_kwh - e_min) / _STEP_KWH - 1e-9))
        i_now = int(round((e_now - e_min) / _STEP_KWH))

        # Slutvärde: energi kvar vid horisontens slut ersätter köp senare, till
        # snittköppriset de sista 24 h. Saknas morgondagens priser (horisonten
        # slutar vid midnatt) värderas energin upp till morgondagens väntade
        # underskott till dagens högsta köppris – samma skydd som V-planerarens
        # golv före prispubliceringen (docs/decisions.md P05).
        tail = [b for b, s in zip(buy[:n_real], slots) if s.start >= slots[-1].end - timedelta(hours=24)]
        ref_buy = float(np.mean(tail)) if tail else float(np.mean(buy))
        terminal = (energies - e_min) * ref_buy * eta_leg
        tomorrow_start = (now_a + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if not any(s.start >= tomorrow_start for s in slots):
            tomorrow_need = max(0.0, level_on(tomorrow_start + timedelta(hours=12)) - solar_forecast_tomorrow_kwh)
            floor_e = min(e_max, e_min + tomorrow_need)
            protected = np.minimum(energies, floor_e) - e_min
            terminal += np.maximum(0.0, protected) * (float(np.max(buy)) - ref_buy) * eta_leg
        cost_to_go = -terminal

        state_idx = np.arange(n_states)[:, None]
        policy = np.zeros((n, n_states), dtype=np.int16)
        values_t0 = None
        for t in range(n - 1, -1, -1):
            h = hours[t]
            k_up = int(math.floor(battery_max_power_kw * h * eta_leg / _STEP_KWH + 1e-9))
            k_dn = int(math.floor(battery_max_power_kw * h / eta_leg / _STEP_KWH + 1e-9))
            ks = np.arange(-k_dn, k_up + 1)
            ac = np.where(ks > 0, ks * _STEP_KWH / eta_leg, ks * _STEP_KWH * eta_leg)  # kWh från nätets sida
            grid = net[t] + ac
            step_cost = (np.where(grid > 0, grid * buy[t], grid * sell[t])
                         + np.where(ac < 0, -ac * self.cycle_cost_sek_kwh, 0.0))
            nxt = state_idx + ks[None, :]
            valid = (nxt >= 0) & (nxt < n_states)
            # Under reserven får batteriet bara laddas, aldrig tömmas vidare.
            valid &= ~((ks[None, :] < 0) & (nxt < i_reserve))
            total = step_cost[None, :] + cost_to_go[np.clip(nxt, 0, n_states - 1)]
            total = np.where(valid, total, np.inf)
            # Vid lika kostnad: välj den åtgärd som rör batteriet minst.
            total = total + _TIE_EPS * np.abs(ks)[None, :]
            best = np.argmin(total, axis=1)
            policy[t] = ks[best]
            cost_to_go = total[np.arange(n_states), best] - _TIE_EPS * np.abs(ks[best])
            if t == 0:
                values_t0 = cost_to_go

        # Marginalvärde vid nuvarande nivå (kr per lagrad kWh) – för kortet och
        # varmvattendumpen, som jämför V_charge (per AC-kWh in) med säljpriset.
        i_lo, i_hi = max(0, i_now - 1), min(n_states - 1, i_now + 1)
        v_stored = float((values_t0[i_lo] - values_t0[i_hi]) / max(_STEP_KWH, (i_hi - i_lo) * _STEP_KWH))

        planned: list[PlannedSlot] = []
        i = i_now
        exported_kwh = 0.0
        revenue = 0.0
        for t, s in enumerate(slots):
            k = int(policy[t][i])
            h = hours[t]
            ac = k * _STEP_KWH / eta_leg if k > 0 else k * _STEP_KWH * eta_leg
            power_w = ac / h * 1000.0
            deficit = net[t]
            soc_pct = energies[i] / cap * 100.0
            if k > 0:
                action = "solar_charge" if deficit < 0 and ac <= -deficit + 1e-6 else "grid_charge"
                reason = f"DP: laddar {power_w:.0f}W (köp {buy[t]:.2f}, sälj {sell[t]:.2f} kr)"
            elif k < 0:
                battery_export = max(0.0, -ac - max(deficit, 0.0))
                # Exekutorns export laddar ur |målvärde| + husets underskott, så
                # målvärdet är bara exportdelen. Ett överskott mindre än ett
                # energisteg är avrundning, inte en exportaffär.
                if battery_export > _STEP_KWH * eta_leg:
                    action = "export"
                    power_w = -battery_export / h * 1000.0
                    exported_kwh += battery_export
                    revenue += battery_export * sell[t]
                    reason = (f"DP: säljer {-power_w:.0f}W ur batteriet + täcker huset "
                              f"(sälj {sell[t]:.2f}, köp {buy[t]:.2f} kr)")
                else:
                    action = "cover_load"
                    reason = f"DP: laddar ur {-power_w:.0f}W (köp {buy[t]:.2f}, sälj {sell[t]:.2f} kr)"
            else:
                action = "idle"
                reason = (f"DP: håller batteriet, köper {deficit / h * 1000:.0f}W ({buy[t]:.2f} kr)"
                          if deficit > 0.01 else f"DP: vilar (sälj {sell[t]:.2f} kr)")
            planned.append(PlannedSlot(
                start=s.start, end=s.end, action=action, target_power_w=power_w,
                battery_soc_est_pct=round(soc_pct, 1), reason=reason,
                hold_battery=(k == 0 and deficit > 0.01),
            ))
            i = min(max(i + k, 0), n_states - 1)

        notes = (f"DP: SOC {battery_soc_pct:.0f}% → {energies[i] / cap * 100:.0f}% | "
                 f"V={v_stored:.2f}kr/kWh lagrad | slutvärde {ref_buy * eta_leg:.2f}kr/kWh | "
                 f"reserv {reserve_kwh:.1f}kWh | export {exported_kwh:.1f}kWh")
        return DayPlan(
            generated_at=now_a,
            valid_until=now_a + timedelta(minutes=15),
            evening_target_soc_pct=reserve_kwh / cap * 100.0,
            export_floor_kwh=reserve_kwh,
            total_exportable_kwh=exported_kwh,
            expected_revenue_sek=revenue,
            solar_takeover_dt=solar_takeover_dt,
            hourly_load_kw=hourly_load_kw,
            pv_production_ratio=pv_production_ratio,
            marginal_value_sek_kwh=v_stored,
            marginal_value_charge_sek_kwh=v_stored * eta_leg,
            slots=planned,
            notes=notes,
        )
