"""Beslutstexten utan effektsiffror (v0.9.86).

Exekutorn skriver effekten med tecken ("+0W", "-2333W"); sensorn tog bara bort
minustecknet, så "håller batteriet +0W" blev "håller batteriet +".
"""
from custom_components.smart_energy_manager.sensor import _POWER_VALUE_RE


def _strip(text):
    return _POWER_VALUE_RE.sub("", text)


def test_signed_power_values_are_removed_without_leftover_sign():
    assert _strip("Auto mode | Plan idle: håller batteriet +0W | DP: håller batteriet, köper 1200W (2.16 kr)") == \
        "Auto mode | Plan idle: håller batteriet | DP: håller batteriet, köper (2.16 kr)"
    assert _strip("Plan cover_load: egenförbrukning -2333W | DP: laddar ur 2267W") == \
        "Plan cover_load: egenförbrukning | DP: laddar ur"
    assert _strip("Plan grid_charge: nätladdar +6400.5W |") == "Plan grid_charge: nätladdar |"


def test_prices_and_percent_are_kept():
    assert _strip("DP: SOC 31% → 16% | köp 2.68, sälj 1.46 kr") == "DP: SOC 31% → 16% | köp 2.68, sälj 1.46 kr"
