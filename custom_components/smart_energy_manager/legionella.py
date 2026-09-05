"""Legionella-desinficering för Smart Energy Manager.

Kör pannans legionella-program (digital switch) ca 1 gång/vecka för att
värma varmvattnet till ≥65°C och eliminera legionellabakterier.

Pannans beteende:
  - Vi slår PÅ switchen för att starta programmet
  - Pannan avslutar programmet och slår AV switchen automatiskt när klart
  - Om vi slår av i förtid avbryts cykeln
  - Vi bekräftar lyckad körning via temperatursensorn (≥ target_temp)

Prioritetsordning för start:
  1. Solöverskott (primärt val) inom önskat tidsfönster
  2. Lågt spotpris inom önskat tidsfönster
  3. Nödkörning om intervallet överskridits med 50% (undviker natten 23-06)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .price_scheduler import PriceSchedule
from .const import (
    DOMAIN,
    CONF_LEGIONELLA_ENABLED, CONF_LEGIONELLA_INTERVAL_DAYS,
    CONF_LEGIONELLA_PREFERRED_HOUR_START, CONF_LEGIONELLA_PREFERRED_HOUR_END,
    CONF_LEGIONELLA_MAX_PRICE, CONF_LEGIONELLA_DURATION_MINUTES,
    CONF_LEGIONELLA_TARGET_TEMP,
    DEFAULT_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_INTERVAL_DAYS,
    DEFAULT_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_END,
    DEFAULT_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_DURATION_MINUTES,
    DEFAULT_LEGIONELLA_TARGET_TEMP,
)

_LOGGER = logging.getLogger(__name__)
STORAGE_KEY = f"{DOMAIN}.legionella"
STORAGE_VERSION = 1


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
        buy_price: float,
        switch_is_on: Optional[bool],  # pannans legionella-switch: True/False, None = tillfälligt otillgänglig
        water_temp: Optional[float],  # ackumulatortank-temperatur (°C) eller None
        price_schedule: Optional[PriceSchedule] = None,
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
        interval_days = int(self._config.get(CONF_LEGIONELLA_INTERVAL_DAYS, DEFAULT_LEGIONELLA_INTERVAL_DAYS))
        if self._last_run is None:
            days_since = interval_days + 1
            due = True
            overdue = True
        else:
            days_since = (now - self._last_run).total_seconds() / 86400
            today = now.date() if hasattr(now, "date") else now.astimezone().date()
            due_date = (self._last_run + timedelta(days=interval_days)).date()
            overdue_date = (self._last_run + timedelta(days=int(interval_days * 1.5))).date()
            due = today >= due_date
            overdue = today >= overdue_date

        if not due:
            return False, f"legionella: {days_since:.1f}/{interval_days} dagar sedan senaste"

        hour = now.hour
        hour_start = int(self._config.get(CONF_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_START))
        hour_end   = int(self._config.get(CONF_LEGIONELLA_PREFERRED_HOUR_END,   DEFAULT_LEGIONELLA_PREFERRED_HOUR_END))
        max_price  = float(self._config.get(CONF_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_MAX_PRICE))

        in_preferred_window = hour_start <= hour < hour_end
        # P5-4: planera in i den billigaste tredjedelen eller soligaste sloten
        # inom fönstret, istället för att trigga på första ögonblick som råkar
        # uppfylla ett fast tröskelvärde. 6 kW (inte 3 kW) matchar elpatronernas
        # uttag – annars var solkravet lägre än vad de själva drar.
        if price_schedule is not None:
            is_opportunity, opp_reason = price_schedule.is_best_opportunity_now(
                now, hour_start, hour_end, solar_threshold_kw=6.0,
            )
            solar_ok = is_opportunity and "sol" in opp_reason
            cheap_ok = is_opportunity and not solar_ok
        else:
            # Fallback utan prisschema: gamla absoluta trösklar.
            solar_ok = solar_surplus_w >= 6000 and in_preferred_window
            cheap_ok = buy_price <= max_price and in_preferred_window
        emergency_ok = overdue and (6 <= hour < 23)

        if solar_ok:
            reason = f"legionella: startar på solöverskott ({solar_surplus_w:.0f}W)"
        elif cheap_ok:
            reason = f"legionella: startar på lågt pris ({buy_price:.3f} SEK)"
        elif emergency_ok:
            reason = f"legionella: nödstart ({days_since:.1f} dagar sedan senaste)"
        else:
            return False, (
                f"legionella: väntar (sol={solar_surplus_w:.0f}W "
                f"pris={buy_price:.3f} timme={hour})"
            )

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
        interval_days = int(self._config.get(CONF_LEGIONELLA_INTERVAL_DAYS, DEFAULT_LEGIONELLA_INTERVAL_DAYS))
        return self._last_run + timedelta(days=interval_days)

    def update_config(self, config: dict) -> None:
        self._config = config
