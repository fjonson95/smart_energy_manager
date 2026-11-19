"""Snötäckta solpaneler: planens solprognos räknas ned (2026-11-19).

Solcast vet inte att panelerna är täckta. 19 nov 2026 gav de 0,7 kWh mot
prognosen 6,0 kWh, och planen räknade med ~9 kWh sol nästa dag till
kvällstoppen i stället för att nätladda billigt på natten. V-planeraren hade
ett skydd (riskpåslag på V vid låg produktionskvot och kyla); det försvann med
V i v0.9.81. Här mäts täckningen under dagen och förs framåt med SMHI:s
dygnsprognos – samma fysik som V:s _simulate_drought_days (användarens
iakttagelser 2026-09-09): snö fastnar bara mellan −5 och +5 °C, och täckta
paneler blir fria när dygnsmax når 1,5 °C. Ren logik utan HA-beroenden.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

COVER_RATIO = 0.30           # faktisk/prognos hittills under detta (och kyla) → täckt
MIN_FORECAST_KWH = 1.0       # så mycket prognos hittills krävs för att bedöma
CLEAR_RATIO = 0.50           # senaste timmen över detta → fri igen
MIN_HOUR_FORECAST_KWH = 0.3  # så mycket prognos senaste timmen krävs för att bedöma
RECOVERY_TEMP_C = 1.5
STICK_MIN_C = -5.0
STICK_MAX_C = 5.0
NEW_SNOW_RATIO = 0.15        # ny snö i prognosen utan egen mätning (19 nov: 0,12)
_SNOWY = frozenset({"snowy", "snowy-rainy"})


@dataclass
class SnowState:
    covered: bool = False
    ratio: float = 1.0          # uppmätt faktisk/prognos medan täckt
    since: Optional[str] = None  # ISO-datum då täckningen upptäcktes


def update_snow_state(
    state: SnowState,
    today: date,
    actual_today_kwh: Optional[float],
    forecast_so_far_kwh: float,
    actual_last_hour_kwh: Optional[float],
    forecast_last_hour_kwh: float,
    outdoor_temp_c: Optional[float],
) -> SnowState:
    """Nytt tillstånd efter dagens mätning. Utan tillräcklig prognos att mäta
    mot (natt, tidig morgon) behålls tillståndet – täckningen förs över natten."""
    if (state.covered and actual_last_hour_kwh is not None
            and forecast_last_hour_kwh >= MIN_HOUR_FORECAST_KWH
            and actual_last_hour_kwh >= CLEAR_RATIO * forecast_last_hour_kwh):
        return SnowState()
    if actual_today_kwh is None or forecast_so_far_kwh < MIN_FORECAST_KWH:
        return state
    ratio = max(0.0, actual_today_kwh / forecast_so_far_kwh)
    if ratio < COVER_RATIO and outdoor_temp_c is not None and outdoor_temp_c <= 0.0:
        return SnowState(True, round(ratio, 2), state.since if state.covered else today.isoformat())
    if state.covered and ratio >= COVER_RATIO:
        return SnowState()
    if state.covered:
        # Fortfarande lågt men plusgrader: behåll täckningen med dagens kvot.
        return SnowState(True, round(ratio, 2), state.since)
    return state


def solar_factors(
    state: SnowState,
    today: date,
    forecast_days: Optional[dict],
    horizon_days: int = 2,
) -> dict:
    """{datum: faktor} för dygn då panelerna väntas vara täckta; övriga dygn
    saknas (faktor 1). forecast_days: {datum: (condition, tmax, tmin)} från
    SMHI. Ett dygn utan prognos ärver föregående dygns tillstånd."""
    factors: dict = {}
    covered = state.covered
    ratio = state.ratio if state.covered else NEW_SNOW_RATIO
    if covered:
        factors[today] = ratio
    for i in range(1, horizon_days + 1):
        d = today + timedelta(days=i)
        day = (forecast_days or {}).get(d)
        if day is not None:
            condition, tmax, tmin = day
            if covered and tmax >= RECOVERY_TEMP_C:
                covered = False
            elif not covered and condition in _SNOWY and tmin <= STICK_MAX_C and tmax >= STICK_MIN_C:
                covered = True
        if covered:
            factors[d] = ratio
    return factors
