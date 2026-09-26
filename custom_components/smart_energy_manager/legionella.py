"""Legionella-desinficering för Smart Energy Manager.

Kör pannans legionella-program (digital switch) med ett rörligt intervall
(standard 5–9 dagar) för att värma varmvattnet till ≥65°C och eliminera
legionellabakterier.

Pannans beteende:
  - Vi slår PÅ switchen för att starta programmet
  - Pannan avslutar programmet och slår AV switchen automatiskt när klart
  - Om vi slår av i förtid avbryts cykeln
  - Vi bekräftar lyckad körning via temperatursensorn (≥ target_temp)

Startval: från min-dagen väljs billigaste körfönstret (körtid × effektivt
pris) bland de kvartar vars priser redan är publicerade, dygnets alla timmar.
Effektivt pris räknar solöverskottet (pessimistiskt p10 minus husets last) som
värt säljpriset istället för köppriset. Senast på max-dagen körs det bästa
kända fönstret oavsett pris.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .price_scheduler import PriceSchedule
from .const import (
    DOMAIN,
    CONF_LEGIONELLA_ENABLED, CONF_LEGIONELLA_MIN_INTERVAL_DAYS, CONF_LEGIONELLA_MAX_INTERVAL_DAYS,
    CONF_LEGIONELLA_DURATION_MINUTES,
    CONF_LEGIONELLA_TARGET_TEMP,
    DEFAULT_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_MIN_INTERVAL_DAYS, DEFAULT_LEGIONELLA_MAX_INTERVAL_DAYS,
    DEFAULT_LEGIONELLA_DURATION_MINUTES,
    DEFAULT_LEGIONELLA_TARGET_TEMP,
)

_LOGGER = logging.getLogger(__name__)
STORAGE_KEY = f"{DOMAIN}.legionella"
STORAGE_VERSION = 1

# Elpatronernas uttag under körningen (samma 6 kW som hetvattendumpen använder).
_BOILER_LOAD_KW = 6.0
# Före max-dagen körs bara om bästa fönstret är klart billigare än snittet av
# det kända – annars väntas en billigare dag in (priser publiceras ~36–48 h
# framåt, så spannet 5–9 dagar kan bara utnyttjas genom att vänta).
_CHEAP_FACTOR = 0.9
# En framtida soldag (bortom prishorisonten) måste vara minst 10 % billigare
# än bästa kända fönster för att vi ska vänta in den – prognosen är osäker.
_FUTURE_GAIN = 0.9
# Under 12 h känd prisdata går det inte att bedöma om något är billigt.
_MIN_KNOWN_HOURS = 12.0


class LegionellaManager:
    """Hanterar schema och körning av legionelladesinficering."""

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        self._hass = hass
        self._config = config
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._last_run: Optional[datetime] = None
        self._running: bool = False
        self._run_started_at: Optional[datetime] = None
        self._loaded: bool = False
        # Spåra om vi redan bekräftat via temperatur denna körning
        self._temp_confirmed: bool = False

    # ── Persistens ────────────────────────────────────────────────────

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data and "last_run" in data:
            try:
                self._last_run = datetime.fromisoformat(data["last_run"])
                _LOGGER.debug("Legionella: senaste körning %s", self._last_run)
            except ValueError:
                self._last_run = None
        if data and data.get("running") and data.get("run_started_at"):
            try:
                self._running = True
                self._run_started_at = datetime.fromisoformat(data["run_started_at"])
                self._temp_confirmed = bool(data.get("temp_confirmed", False))
                _LOGGER.info(
                    "Legionella: återställer pågående körning (startad %s)",
                    self._run_started_at,
                )
            except ValueError:
                pass
        self._loaded = True

    async def _async_save(self) -> None:
        await self._store.async_save({
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "running": self._running,
            "run_started_at": self._run_started_at.isoformat() if self._run_started_at else None,
            "temp_confirmed": self._temp_confirmed,
        })

    # ── Huvudmetod (anropas varje koordinatorcykel) ───────────────────

    def should_run_now(
        self,
        now: datetime,
        solar_surplus_w: float,
        switch_is_on: Optional[bool],  # pannans legionella-switch: True/False, None = tillfälligt otillgänglig
        water_temp: Optional[float],  # ackumulatortank-temperatur (°C) eller None
        price_schedule: Optional[PriceSchedule] = None,
        house_load_w: float = 0.0,
        future_solar: Optional[list[tuple[datetime, float]]] = None,  # (timstart, p10-kW) för dagar bortom prishorisonten
    ) -> tuple[bool, str]:
        """
        Returnera (ska_hålla_switch_på, orsak).

        Logik:
        - Om switchen redan är på (pannan kör): övervaka tills pannan slår av.
          Kontrollera temperaturen för att bekräfta lyckad körning.
        - Om switchen är av och det är dags: slå på.
        - Om switchen är av och pannan precis slog av den: kontrollera om
          temperaturen bekräftar att körningen lyckades → uppdatera last_run.
        - Om switchen är tillfälligt otillgänglig (unavailable/unknown): rör inte
          körningsstatus alls – annars misstolkas ett kort kommunikationsglapp
          som att pannan avslutat körningen.
        """
        if not self._loaded:
            return False, "legionella: storage ej laddad"

        if not self._config.get(CONF_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_ENABLED):
            return False, "legionella: avaktiverat"

        target_temp = float(self._config.get(CONF_LEGIONELLA_TARGET_TEMP, DEFAULT_LEGIONELLA_TARGET_TEMP))

        # ── Switchen tillfälligt otillgänglig – håll nuvarande status ────
        if switch_is_on is None:
            if self._running:
                elapsed_min = (now - self._run_started_at).total_seconds() / 60 if self._run_started_at else 0
                return True, f"legionella: pågår ({elapsed_min:.0f} min, switch otillgänglig)"
            return False, "legionella: switch tillfälligt otillgänglig"

        # ── Switchen är PÅ – pannan kör programmet ───────────────────
        if switch_is_on:
            if not self._running:
                # Switchen slogs på externt (eller vi missade starten, t.ex. efter omladdning)
                self._running = True
                self._run_started_at = now
                self._temp_confirmed = False
                self._hass.async_create_task(self._async_save())
                _LOGGER.info("Legionella: switch är på – synkar körning")

            # Bekräfta via temperatur om vi inte redan gjort det
            if water_temp is not None and water_temp >= target_temp and not self._temp_confirmed:
                self._temp_confirmed = True
                self._hass.async_create_task(self._async_save())
                _LOGGER.info("Legionella: temperatur %.1f°C ≥ %.1f°C – körning bekräftad", water_temp, target_temp)

            elapsed_min = (now - self._run_started_at).total_seconds() / 60 if self._run_started_at else 0
            return True, f"legionella: pågår ({elapsed_min:.0f} min, temp={water_temp:.1f}°C)" if water_temp is not None else f"legionella: pågår ({elapsed_min:.0f} min)"

        # ── Switchen är AV ────────────────────────────────────────────
        if self._running:
            # Pannan slog precis av switchen → körning avslutad
            self._running = False
            elapsed_min = (now - self._run_started_at).total_seconds() / 60 if self._run_started_at else 0

            if self._temp_confirmed:
                # Lyckad körning – uppdatera last_run
                self._last_run = now
                self._hass.async_create_task(self._async_save())
                _LOGGER.info(
                    "Legionella: körning klar (%.0f min), temp bekräftad – last_run uppdaterad",
                    elapsed_min,
                )
                return False, "legionella: klar och bekräftad ✓"
            else:
                # Switchen stängdes av men temp nådde aldrig målet
                # → räkna inte som lyckad körning
                self._hass.async_create_task(self._async_save())
                _LOGGER.warning(
                    "Legionella: körning avbröts efter %.0f min utan att nå %.1f°C "
                    "(nuvarande temp: %s°C) – last_run uppdateras INTE",
                    elapsed_min,
                    target_temp,
                    f"{water_temp:.1f}" if water_temp is not None else "okänd",
                )
                return False, "legionella: avbruten (temp ej bekräftad)"

        # ── Bedöm om det är dags att starta ──────────────────────────
        min_days = int(self._config.get(CONF_LEGIONELLA_MIN_INTERVAL_DAYS, DEFAULT_LEGIONELLA_MIN_INTERVAL_DAYS))
        max_days = max(min_days, int(self._config.get(CONF_LEGIONELLA_MAX_INTERVAL_DAYS, DEFAULT_LEGIONELLA_MAX_INTERVAL_DAYS)))
        today = now.date()
        if self._last_run is None:
            days_since = float(max_days)
            due = True
            deadline_reached = True
            deadline_end = now - timedelta(days=1)
        else:
            days_since = (now - self._last_run).total_seconds() / 86400
            due = today >= (self._last_run + timedelta(days=min_days)).date()
            deadline_date = (self._last_run + timedelta(days=max_days)).date()
            deadline_reached = today >= deadline_date
            deadline_end = datetime.combine(deadline_date + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)

        if not due:
            return False, f"legionella: {days_since:.1f} dagar sedan senaste (körs tidigast efter {min_days}, senast {max_days})"

        slots = [s for s in (price_schedule.slots if price_schedule else []) if s.end > now]
        if not slots:
            # Utan prisschema: bara solöverskott eller nödstart på dagtid.
            if solar_surplus_w >= _BOILER_LOAD_KW * 1000:
                reason = f"legionella: startar på solöverskott ({solar_surplus_w:.0f}W)"
            elif deadline_reached and 6 <= now.hour < 23:
                reason = f"legionella: nödstart ({days_since:.1f} dagar sedan senaste, prisdata saknas)"
            else:
                return False, f"legionella: väntar (prisdata saknas, sol={solar_surplus_w:.0f}W)"
        else:
            house_kw = max(0.0, house_load_w) / 1000.0

            def _effective_price(s) -> float:
                slot_h = (s.end - s.start).total_seconds() / 3600.0
                surplus_kw = max(0.0, s.solar_kwh_p10 / slot_h - house_kw) if slot_h > 0 else 0.0
                covered = min(1.0, surplus_kw / _BOILER_LOAD_KW)
                return covered * s.sell_sek + (1.0 - covered) * s.buy_sek

            slot_min = (slots[0].end - slots[0].start).total_seconds() / 60.0
            n = max(1, math.ceil(float(self._config.get(CONF_LEGIONELLA_DURATION_MINUTES, DEFAULT_LEGIONELLA_DURATION_MINUTES)) / slot_min))
            eff = [_effective_price(s) for s in slots]
            window_cost = [sum(eff[i:i + n]) / n for i in range(len(slots) - n + 1)]
            allowed = [i for i in range(len(window_cost)) if slots[i].start < deadline_end]
            known_hours = (slots[-1].end - now).total_seconds() / 3600.0

            if not allowed:
                if not deadline_reached:
                    return False, "legionella: väntar (inget fullständigt körfönster i känd prisdata)"
                if not 6 <= now.hour < 23:
                    return False, "legionella: nödstart väntar till 06 (undviker natten)"
                reason = f"legionella: nödstart ({days_since:.1f} dagar sedan senaste)"
            else:
                best_i = min(allowed, key=lambda i: window_cost[i])
                best_cost = window_cost[best_i]
                mean_cost = sum(eff) / len(eff)
                start_txt = slots[best_i].start.astimezone().strftime("%d %H:%M")

                # Bortom prishorisonten (~36–48 h) finns bara solprognos. Är en
                # sådan dag klart billigare (solen täcker patronerna, värderat
                # till säljpris mot snittköppris) väntar vi in den.
                if future_solar and not deadline_reached:
                    horizon_end = slots[-1].end
                    buy_mean = sum(s.buy_sek for s in slots) / len(slots)
                    sell_mean = sum(s.sell_sek for s in slots) / len(slots)
                    hours = max(1, math.ceil(n * slot_min / 60.0))
                    by_start = {t: kw for t, kw in future_solar if t >= horizon_end and t < deadline_end}
                    best_future: Optional[tuple[float, datetime]] = None
                    for t in sorted(by_start):
                        span = [by_start.get(t + timedelta(hours=k)) for k in range(hours)]
                        if any(v is None for v in span):
                            continue
                        covered = sum(min(1.0, max(0.0, v - house_kw) / _BOILER_LOAD_KW) for v in span) / hours
                        est = covered * sell_mean + (1.0 - covered) * buy_mean
                        if best_future is None or est < best_future[0]:
                            best_future = (est, t)
                    if best_future is not None and best_future[0] <= _FUTURE_GAIN * best_cost:
                        return False, (
                            f"legionella: väntar på soligare dag {best_future[1].astimezone().strftime('%d %H:%M')} "
                            f"(uppskattat {best_future[0]:.2f} kr/kWh mot {best_cost:.2f} bästa kända pris)"
                        )

                if not deadline_reached and known_hours >= _MIN_KNOWN_HOURS and best_cost > _CHEAP_FACTOR * mean_cost:
                    return False, (
                        f"legionella: väntar på billigare dag (bästa kända {best_cost:.2f} kr/kWh "
                        f"{start_txt}, snitt {mean_cost:.2f}, {days_since:.1f}/{max_days} dagar)"
                    )
                if window_cost[0] > best_cost + 1e-6:
                    return False, (
                        f"legionella: väntar på bästa fönstret {start_txt} "
                        f"({best_cost:.2f} kr/kWh mot {window_cost[0]:.2f} nu)"
                    )
                reason = f"legionella: startar (fönsterpris {window_cost[0]:.2f} kr/kWh, snitt {mean_cost:.2f})"

        # Starta – markera INTE _running här. Om coordinatorns switch.turn_on
        # misslyckas (nätverk nere, enheten svarar inte) skulle managern annars
        # tro att en körning pågår trots att pannan aldrig fick kommandot, och
        # sluta försöka igen nästa cykel. "Switchen är PÅ"-grenen ovan är den
        # enda platsen som sätter _running – den bygger på OBSERVERAT
        # tillstånd, inte på att tjänsteanropet lyckades, vilket är ett
        # starkare bevis än ett API-svar. Så länge switchen förblir av
        # returnerar vi samma "starta"-beslut varje cykel, vilket ger gratis
        # återförsök av ett misslyckat anrop.
        _LOGGER.info("Legionella: %s", reason)
        return True, reason

    # ── Status-properties ─────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_run(self) -> Optional[datetime]:
        return self._last_run

    @property
    def days_since_last_run(self) -> Optional[float]:
        if self._last_run is None:
            return None
        return (dt_util.now() - self._last_run).total_seconds() / 86400

    def next_due(self) -> Optional[datetime]:
        if self._last_run is None:
            return dt_util.now()
        min_days = int(self._config.get(CONF_LEGIONELLA_MIN_INTERVAL_DAYS, DEFAULT_LEGIONELLA_MIN_INTERVAL_DAYS))
        return self._last_run + timedelta(days=min_days)

    def update_config(self, config: dict) -> None:
        self._config = config
