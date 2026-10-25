"""Övre gräns: perfekt framförhållning (känner hela periodens priser, sol och last).

Dynamisk programmering över (kvart, lagrad energi) på en backtest-CSV, med
samma batterimodell som testdata/backtest.py (η delat lika på laddning och
urladdning, cykelkostnad per urladdad kWh) och SETTINGS därifrån. Ignorerar
reserv, fasgränser och EV – ett tak, inte ett mål (HANDOFF.md bilaga A).

    python testdata/oracle_dp.py backtest_result.csv
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from testdata.backtest import SETTINGS  # noqa: E402


def _f(v: str) -> float:
    return float(v.replace(",", ".")) if v not in ("", None) else 0.0


def oracle(path: str, step_kwh: float = 0.25, action_step_kw: float = 0.5) -> dict:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    buy = np.array([_f(r["buy_sek_kwh"]) for r in rows])
    sell = np.array([_f(r["sell_sek_kwh"]) for r in rows])
    net = np.array([(_f(r["house_load_w"]) - _f(r["solar_w"])) / 1000.0 for r in rows])
    ref_cost = sum(_f(r["ref_cost_sek"]) - _f(r["ref_export_rev_sek"]) for r in rows)

    s = SETTINGS
    cap, pmax, h = s["battery_capacity_kwh"], s["battery_max_power_kw"], 0.25
    eta, cyc = s["eta_roundtrip"] ** 0.5, s["cycle_cost_sek_kwh"]
    energies = np.arange(cap * s["battery_min_soc"] / 100, cap * s["battery_max_soc"] / 100 + 1e-9, step_kwh)
    actions = np.arange(-pmax, pmax + 1e-9, action_step_kw)

    value = np.zeros(len(energies))
    for t in range(len(buy) - 1, -1, -1):
        best = np.full(len(energies), np.inf)
        for a in actions:
            e2 = energies + (a * h * eta if a > 0 else a * h / eta)
            ok = (e2 >= energies[0] - 1e-9) & (e2 <= energies[-1] + 1e-9)
            grid = net[t] + a
            cost = (grid * h * buy[t] if grid > 0 else grid * h * sell[t]) + (-a * h * cyc if a < 0 else 0.0)
            idx = np.clip(np.round((e2 - energies[0]) / step_kwh).astype(int), 0, len(energies) - 1)
            cand = cost + value[idx]
            cand[~ok] = np.inf
            best = np.minimum(best, cand)
        value = best

    soc0 = _f(rows[0]["sim_battery_soc_pct"]) / 100.0
    i0 = int(np.clip(round((soc0 * cap - energies[0]) / step_kwh), 0, len(energies) - 1))
    return {"ref_net_cost_sek": ref_cost, "oracle_net_cost_sek": float(value[i0]),
            "oracle_savings_sek": ref_cost - float(value[i0])}


if __name__ == "__main__":
    r = oracle(sys.argv[1])
    print(f"referens {r['ref_net_cost_sek']:.2f}  orakel {r['oracle_net_cost_sek']:.2f}  "
          f"besparing {r['oracle_savings_sek']:.2f} SEK")
