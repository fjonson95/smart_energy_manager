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
        self._loaded = True

    async def _async_save(self) -> None:
        await self._store.async_save({
            "last_run": self._last_run.isoformat() if self._last_run else None
        })

    # ── Huvudmetod (anropas varje koordinatorcykel) ───────────────────

    def should_run_now(
        self,
        now: datetime,
        solar_surplus_w: float,
        buy_price: float,
        switch_is_on: bool,        # pannans legionella-switch aktuellt tillstånd
        water_temp: Optional[float],  # ackumulatortank-temperatur (°C) eller None
    ) -> tuple[bool, str]:
        """
        Returnera (ska_hålla_switch_på, orsak).

        Logik:
        - Om switchen redan är på (pannan kör): övervaka tills pannan slår av.
          Kontrollera temperaturen för att bekräfta lyckad körning.
        - Om switchen är av och det är dags: slå på.
        - Om switchen är av och pannan precis slog av den: kontrollera om
          temperaturen bekräftar att körningen lyckades → uppdatera last_run.
        """
        if not self._loaded:
            return False, "legionella: storage ej laddad"

        if not self._config.get(CONF_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_ENABLED):
            return False, "legionella: avaktiverat"

        target_temp = float(self._config.get(CONF_LEGIONELLA_TARGET_TEMP, DEFAULT_LEGIONELLA_TARGET_TEMP))

        # ── Switchen är PÅ – pannan kör programmet ───────────────────
        if switch_is_on:
            if not self._running:
                # Switchen slogs på externt (eller vi missade starten)
                self._running = True
                self._run_started_at = now
                self._temp_confirmed = False
                _LOGGER.info("Legionella: switch är på – synkar körning")

            # Bekräfta via temperatur om vi inte redan gjort det
            if water_temp is not None and water_temp >= target_temp and not self._temp_confirmed:
                self._temp_confirmed = True
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
        else:
            days_since = (now - self._last_run).total_seconds() / 86400

        overdue = days_since >= interval_days * 1.5
        due = days_since >= interval_days

        if not due:
            return False, f"legionella: {days_since:.1f}/{interval_days} dagar sedan senaste"

        hour = now.hour
        hour_start = int(self._config.get(CONF_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_START))
        hour_end   = int(self._config.get(CONF_LEGIONELLA_PREFERRED_HOUR_END,   DEFAULT_LEGIONELLA_PREFERRED_HOUR_END))
        max_price  = float(self._config.get(CONF_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_MAX_PRICE))

        in_preferred_window = hour_start <= hour < hour_end
        solar_ok    = solar_surplus_w >= 3000 and in_preferred_window
        cheap_ok    = buy_price <= max_price and in_preferred_window
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

        # Starta
        self._running = True
        self._run_started_at = now
        self._temp_confirmed = False
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
        return (datetime.now().astimezone() - self._last_run).total_seconds() / 86400

    def next_due(self) -> Optional[datetime]:
        if self._last_run is None:
            return datetime.now().astimezone()
        interval_days = int(self._config.get(CONF_LEGIONELLA_INTERVAL_DAYS, DEFAULT_LEGIONELLA_INTERVAL_DAYS))
        return self._last_run + timedelta(days=interval_days)

    def update_config(self, config: dict) -> None:
        self._config = config
