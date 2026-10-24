"""Regressionstest: sommarbacktesten och invarianterna i dess tidslinje.

Kör testdata/backtest.py mot testdata/history (10 dygn, ~10 s). Förväntad
besparing uppdateras medvetet när en ändring ska flytta resultatet, och
siffran skrivs då också i CHANGELOG-posten.
"""
import csv
import os

import pytest

from conftest import REPO_ROOT
from testdata import backtest

EXPECTED_SUMMER_SAVINGS_SEK = 157.18
TOLERANCE_SEK = 1.0


def _f(v: str) -> float:
    return float(v.replace(",", ".")) if v not in ("", None) else 0.0


@pytest.fixture(scope="module")
def summer(tmp_path_factory):
    out = tmp_path_factory.mktemp("bt") / "summer.csv"
    result = backtest.run_backtest(os.path.join(REPO_ROOT, "testdata", "history"), str(out), {})
    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    return result, rows


def test_summer_savings_unchanged(summer):
    result, _ = summer
    assert result["savings_sek"] == pytest.approx(EXPECTED_SUMMER_SAVINGS_SEK, abs=TOLERANCE_SEK)


def test_never_charges_and_discharges_at_once(summer):
    _, rows = summer
    both = [r["timestamp"] for r in rows if _f(r["sim_bat_charge_w"]) > 1 and _f(r["sim_bat_discharge_w"]) > 1]
    assert not both, f"laddar och laddar ur samtidigt: {both[:5]}"


# Backtesten integrerar en hel kvart åt gången, så SOC kan landa en bit
# utanför gränsen efter sista tillåtna kvarten; invarianten är att inget
# beordras när SOC redan ligger vid gränsen.

def test_no_discharge_at_min_soc(summer):
    _, rows = summer
    at_min = [
        r["timestamp"] for r in rows
        if _f(r["sim_battery_soc_pct"]) <= backtest.SETTINGS["battery_min_soc"]
        and _f(r["sim_bat_discharge_w"]) > 1
    ]
    assert not at_min, f"urladdning vid min-SOC: {at_min[:5]}"


@pytest.mark.xfail(strict=True, reason="apply_plan_executor klämmer inte överskottsladdning mot battery_max_soc (fas B)")
def test_no_charge_at_max_soc(summer):
    _, rows = summer
    at_max = [
        r["timestamp"] for r in rows
        if _f(r["sim_battery_soc_pct"]) >= backtest.SETTINGS["battery_max_soc"]
        and _f(r["sim_bat_charge_w"]) > 1
    ]
    assert not at_max, f"laddning vid max-SOC: {at_max[:5]}"
