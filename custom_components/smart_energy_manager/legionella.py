"""Legionella-desinficering för Smart Energy Manager.

Kör elpatronen (2-fas) ca 1 gång/vecka för att värma varmvattnet till ≥60°C
och eliminera legionellabakterier.

Prioritetsordning:
  1. Solöverskott (primärt val)
  2. Lågt spotpris under konfigurerbar gräns
  3. Tidigast möjliga tillfälle om intervallet överskridits (nödkörning)
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
    DEFAULT_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_INTERVAL_DAYS,
    DEFAULT_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_END,
    DEFAULT_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_DURATION_MINUTES,
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

    # ── Persistens ────────────────────────────────────────────────────

    async def async_load(self) -> None:
        """Läs senaste körningstid från disk."""
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

    # ── Beslutspunkt (anropas varje koordinatorcykel) ─────────────────

    def should_run_now(
        self,
        now: datetime,
        solar_surplus_w: float,
        buy_price: float,
    ) -> tuple[bool, str]:
        """Returnera (ska_köra, orsak).

        Anropas från koordinatorn varje cykel. Startar körning om
        villkoren stämmer, avslutar den när durationen är uppnådd.
        """
        if not self._loaded:
            return False, "legionella: storage ej laddad än"

        if not self._config.get(CONF_LEGIONELLA_ENABLED, DEFAULT_LEGIONELLA_ENABLED):
            return False, "legionella: avaktiverat"

        # Om körning pågår – håll på tills duration är slut
        if self._running and self._run_started_at:
            duration_min = int(self._config.get(
                CONF_LEGIONELLA_DURATION_MINUTES, DEFAULT_LEGIONELLA_DURATION_MINUTES
            ))
            elapsed = (now - self._run_started_at).total_seconds() / 60
            if elapsed < duration_min:
                return True, f"legionella: kör ({elapsed:.0f}/{duration_min} min)"
            else:
                # Körning klar
                self._running = False
                self._last_run = now
                self._hass.async_create_task(self._async_save())
                _LOGGER.info("Legionella: körning klar efter %.0f min", elapsed)
                return False, "legionella: precis klar"

        # Beräkna om det är dags
        interval_days = int(self._config.get(
            CONF_LEGIONELLA_INTERVAL_DAYS, DEFAULT_LEGIONELLA_INTERVAL_DAYS
        ))
        if self._last_run is None:
            days_since = interval_days + 1  # aldrig kört → starta ASAP
        else:
            days_since = (now - self._last_run).total_seconds() / 86400

        overdue = days_since >= interval_days * 1.5   # 50% övertid = nödkörning
        due = days_since >= interval_days

        if not due:
            return False, f"legionella: {days_since:.1f}/{interval_days} dagar sedan senaste"

        hour = now.hour
        hour_start = int(self._config.get(
            CONF_LEGIONELLA_PREFERRED_HOUR_START, DEFAULT_LEGIONELLA_PREFERRED_HOUR_START
        ))
        hour_end = int(self._config.get(
            CONF_LEGIONELLA_PREFERRED_HOUR_END, DEFAULT_LEGIONELLA_PREFERRED_HOUR_END
        ))
        max_price = float(self._config.get(
            CONF_LEGIONELLA_MAX_PRICE, DEFAULT_LEGIONELLA_MAX_PRICE
        ))

        in_preferred_window = hour_start <= hour < hour_end

        # Alternativ 1: solöverskott räcker för patronen (≥ 3 kW)
        solar_ok = solar_surplus_w >= 3000

        # Alternativ 2: lågt pris inom önskat tidsfönster
        cheap_ok = buy_price <= max_price and in_preferred_window

        # Alternativ 3: nödkörning – kör oavsett (men undvik natten 23-06)
        emergency_ok = overdue and (6 <= hour < 23)

        if solar_ok and in_preferred_window:
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

        # Starta körning
        self._running = True
        self._run_started_at = now
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
        """Beräknat datum för nästa desinficering."""
        if self._last_run is None:
            return datetime.now().astimezone()
        interval_days = int(self._config.get(
            CONF_LEGIONELLA_INTERVAL_DAYS, DEFAULT_LEGIONELLA_INTERVAL_DAYS
        ))
        return self._last_run + timedelta(days=interval_days)

    def update_config(self, config: dict) -> None:
        self._config = config
