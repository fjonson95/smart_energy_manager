"""Plandatatyper för Smart Energy Manager.

DayPlan och PlannedSlot byggs av dp_planner.DPPlanner (fas B) och läses av
exekutorn, sensorerna, planeringskortet och planloggen. V-planeraren
(EnergyPlanner, regel 1–4 kring marginalvärdet V) togs bort i fas B5.4.1;
den finns kvar i v0.9.80 och i docs/decisions.md P01–P16.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_PLAN_HORIZON_H = 48  # Morgondagens priser finns redan på kvällen när kvällsbesluten fattas.


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
    # slot. Exekutorn får då inte täcka huslasten ur batteriet.
    hold_battery: bool = False
    # Nätdelen av en solar_charge-slot (mål × (1 − solandel)); exekutorn
    # köper den även när solen sviker i stället för att ladda ur.
    grid_charge_w: float = 0.0


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
    # V_charge: det lagrade värdet i ett laddsteg (DP:s värdegradient × √η) -
    # samma tröskel som exekutorn och heat_planner.should_dump_to_hot_water()
    # jämför säljpriset mot.
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
