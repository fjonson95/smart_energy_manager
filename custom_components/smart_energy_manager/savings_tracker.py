"""Realiserad besparing per dygn mot ett hus utan batteri.

Referensen är samma hus med batteriet bortkopplat: nätet hade då fått ta
batteriets laddning/urladdning, dvs. ref_nät = nät − batterieffekt
(batterieffekt positiv = laddning). Allt annat (sol, last, EV, varmvatten)
är detsamma i båda leden, så skillnaden är batteristyrningens värde till
de köp-/säljpriser som gällde just då.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

# Längre glapp än så (omstart, hängning) integreras inte – effekten under
# glappet är okänd och en enda avläsning får inte stå för timmar.
MAX_STEP_S = 120.0
HISTORY_DAYS = 30

_FIELDS = (
    "import_kwh", "export_kwh", "import_cost_sek", "export_revenue_sek",
    "ref_import_kwh", "ref_export_kwh", "ref_import_cost_sek", "ref_export_revenue_sek",
    "battery_charge_kwh", "battery_discharge_kwh",
)


def _empty_day(date: str) -> dict:
    return {"date": date, **{f: 0.0 for f in _FIELDS}}


def day_savings_sek(day: dict) -> float:
    ref = day["ref_import_cost_sek"] - day["ref_export_revenue_sek"]
    actual = day["import_cost_sek"] - day["export_revenue_sek"]
    return ref - actual


class DailySavingsTracker:
    def __init__(self) -> None:
        self.today: Optional[dict] = None
        self.history: list[dict] = []
        self._last_update: Optional[datetime] = None

    def update(self, now: datetime, grid_w: float, battery_w: float, buy_sek: float, sell_sek: float) -> bool:
        """Integrerar en avläsning. grid_w > 0 = import. Returnerar True när
        ett dygn har avslutats och lagts i historiken."""
        # Tidsstämpelns egen zon (dt_util.now() = HA:s tidszon), inte värdens.
        date = (now if now.tzinfo else now.astimezone()).date().isoformat()
        rolled = False
        if self.today is None:
            self.today = _empty_day(date)
        elif self.today["date"] != date:
            self.history.append(self._rounded(self.today))
            self.history = self.history[-HISTORY_DAYS:]
            self.today = _empty_day(date)
            rolled = True
            self._last_update = None

        if self._last_update is not None:
            dt_s = (now - self._last_update).total_seconds()
            if 0.0 < dt_s <= MAX_STEP_S:
                h = dt_s / 3600.0
                self._add(grid_w, h, buy_sek, sell_sek, "")
                self._add(grid_w - battery_w, h, buy_sek, sell_sek, "ref_")
                if battery_w > 0:
                    self.today["battery_charge_kwh"] += battery_w / 1000.0 * h
                else:
                    self.today["battery_discharge_kwh"] += -battery_w / 1000.0 * h
        self._last_update = now
        return rolled

    def pause(self) -> None:
        """Mätvärden saknas just nu – nästa giltiga avläsning startar om
        integrationen istället för att räkna glappet som noll effekt."""
        self._last_update = None

    def _add(self, grid_w: float, h: float, buy_sek: float, sell_sek: float, prefix: str) -> None:
        kwh = grid_w / 1000.0 * h
        d = self.today
        if kwh > 0:
            d[prefix + "import_kwh"] += kwh
            d[prefix + "import_cost_sek"] += kwh * buy_sek
        else:
            d[prefix + "export_kwh"] += -kwh
            d[prefix + "export_revenue_sek"] += -kwh * sell_sek

    @staticmethod
    def _rounded(day: dict) -> dict:
        out = {"date": day["date"], "savings_sek": round(day_savings_sek(day), 2)}
        out.update({f: round(day[f], 3) for f in _FIELDS})
        return out

    def today_summary(self) -> Optional[dict]:
        return self._rounded(self.today) if self.today else None

    def to_dict(self) -> dict:
        return {"today": self.today, "history": self.history}

    def load(self, data: dict) -> None:
        today = data.get("today")
        if isinstance(today, dict) and all(f in today for f in _FIELDS) and "date" in today:
            self.today = today
        history = data.get("history")
        if isinstance(history, list):
            self.history = history[-HISTORY_DAYS:]
