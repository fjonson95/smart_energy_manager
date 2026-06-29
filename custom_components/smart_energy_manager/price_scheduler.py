"""Prisplanering baserad på Nordpool kvartstimmespriser.

Läser raw_today / raw_tomorrow från Nordpool-sensorn och beräknar:
  - Om kommande perioder har negativa/låga priser → proaktiv absorption
  - Bästa urladdningstimmar (höga priser framåt)
  - Bästa laddningstimmar (låga priser framåt, för vinterläge)
  - Antal kvartstimmar med negativt pris framåt

Nordpool-sensorn rapporterar i öre/kWh → vi dividerar med 100 för SEK/kWh.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_LOGGER = logging.getLogger(__name__)

OERE_TO_SEK = 0.01   # öre/kWh → SEK/kWh


@dataclass
class PriceSlot:
    """Ett kvartstimme-prisintervall."""
    start: datetime
    end: datetime
    spot_sek: float          # SEK/kWh (konverterat från öre)
    buy_sek: float = 0.0     # Köppris inkl avgifter/skatt/moms
    sell_sek: float = 0.0    # Säljpris


@dataclass
class PriceSchedule:
    """
    Beräknat prisschema och rekommendationer för kommande period.

    Används av EnergyController för framåtblickande beslut.
    """
    # Alla slots sorterade på tid (idag + imorgon om tillgängligt)
    slots: list[PriceSlot] = field(default_factory=list)

    # Antal kvartstimmar FRAMÅT (från nu) med negativt säljpris
    negative_slots_ahead: int = 0

    # Antal kvartstimmar FRAMÅT med säljpris under low_price_threshold
    low_price_slots_ahead: int = 0

    # Proaktiv absorption rekommenderas (negativa priser väntar)
    should_absorb_proactively: bool = False

    # Bästa urladdningstimme kommande 12h (högst buy_sek)
    best_discharge_slot: Optional[PriceSlot] = None

    # Bästa laddningstimme kommande 12h (lägst buy_sek)
    best_charge_slot: Optional[PriceSlot] = None

    # Genomsnittspris kommande 4h (16 slots)
    avg_price_next_4h: float = 0.0

    # Maximalt pris kommande 12h
    max_price_next_12h: float = 0.0

    # Minimalt pris kommande 12h
    min_price_next_12h: float = 0.0

    # Proaktiv reservkapacitet som bör skapas i batteriet (0–1.0)
    # Ex: 0.2 = håll 20% extra utrymme för kommande absorption
    recommended_headroom: float = 0.0


class PriceScheduler:
    """
    Analyserar Nordpool kvartstimmespriser och ger styrningsrekommendationer.

    Används av koordinatorn varje cykel för att komplettera momentana beslut
    med framåtblickande logik.
    """

    def __init__(
        self,
        grid_fees_sek: float = 0.45,
        energy_tax_sek: float = 0.536,
        vat_rate: float = 0.25,
        extra_revenue_sek: float = 0.07,
        negative_threshold_sek: float = 0.0,
        low_price_threshold_sek: float = 0.30,
        proactive_absorption_slots: int = 4,  # Reagera om ≥ N negativa slots inom 2h
    ):
        self.grid_fees = grid_fees_sek
        self.energy_tax = energy_tax_sek
        self.vat = vat_rate
        self.extra_revenue = extra_revenue_sek
        self.negative_threshold = negative_threshold_sek
        self.low_price_threshold = low_price_threshold_sek
        self.proactive_absorption_slots = proactive_absorption_slots

    def _buy_price(self, spot_sek: float) -> float:
        return (spot_sek + self.grid_fees + self.energy_tax) * (1 + self.vat)

    def _sell_price(self, spot_sek: float) -> float:
        return spot_sek + self.extra_revenue

    def parse_nordpool_attributes(self, attributes: dict, now: datetime) -> list[PriceSlot]:
        """
        Parsa raw_today och raw_tomorrow från Nordpool-attributen.
        Returnerar sorterad lista av PriceSlot framåt i tid.

        Hanterar:
          - MappingProxyType (HA cachar attribut som immutable mappings)
          - Både öre/kWh och SEK/kWh beroende på Nordpool-version
          - Tidszoner (slots från Nordpool har UTC-offset, t.ex. +02:00)
        """
        slots: list[PriceSlot] = []
        now_aware = now if now.tzinfo else now.astimezone()

        # Detektera enhet – om price_in_cents=True är värdet i öre
        price_in_cents = attributes.get("price_in_cents", True)
        scale = OERE_TO_SEK if price_in_cents else 1.0

        parsed_count = 0
        for key in ("raw_today", "raw_tomorrow"):
            raw = attributes.get(key, [])
            if not raw:
                _LOGGER.debug("PriceScheduler: attribut '%s' saknas eller tomt", key)
                continue
            for entry in raw:
                try:
                    # Stöd både dict och MappingProxyType
                    start_raw = entry.get("start") if hasattr(entry, "get") else entry["start"]
                    end_raw   = entry.get("end")   if hasattr(entry, "get") else entry["end"]
                    value     = entry.get("value") if hasattr(entry, "get") else entry["value"]

                    if start_raw is None or end_raw is None or value is None:
                        continue

                    # Nordpool kan returnera antingen datetime-objekt eller ISO-strängar
                    if isinstance(start_raw, datetime):
                        start = start_raw
                    else:
                        start = datetime.fromisoformat(str(start_raw))

                    if isinstance(end_raw, datetime):
                        end = end_raw
                    else:
                        end = datetime.fromisoformat(str(end_raw))

                    # Säkerställ timezone-aware
                    if start.tzinfo is None:
                        start = start.astimezone()
                    if end.tzinfo is None:
                        end = end.astimezone()

                    spot_sek = float(value) * scale
                    slot = PriceSlot(
                        start=start,
                        end=end,
                        spot_sek=spot_sek,
                        buy_sek=self._buy_price(spot_sek),
                        sell_sek=self._sell_price(spot_sek),
                    )
                    slots.append(slot)
                    parsed_count += 1
                except (KeyError, ValueError, TypeError, AttributeError) as e:
                    _LOGGER.debug("Kunde inte parsa Nordpool-slot %s: %s", entry, e)

        _LOGGER.debug("PriceScheduler: parsade %d slots totalt", parsed_count)

        if not slots:
            _LOGGER.warning(
                "PriceScheduler: inga slots parsades – kontrollera att Nordpool-sensorn "
                "har attributen raw_today/raw_tomorrow. Tillgängliga attribut: %s",
                list(attributes.keys()),
            )
            return []

        # Sortera och filtrera: behåll pågående och framtida slots
        slots.sort(key=lambda s: s.start)
        future = [s for s in slots if s.end > now_aware]
        _LOGGER.debug(
            "PriceScheduler: %d slots framåt (av %d totalt), nu=%s",
            len(future), len(slots), now_aware.isoformat()
        )
        return future

    def compute(self, attributes: dict, now: datetime) -> PriceSchedule:
        """
        Beräkna komplett prisschema och rekommendationer.

        Anropas av koordinatorn varje cykel.
        """
        schedule = PriceSchedule()
        slots = self.parse_nordpool_attributes(attributes, now)
        schedule.slots = slots

        if not slots:
            _LOGGER.debug("PriceScheduler: inga slots tillgängliga")
            return schedule

        now_aware = now if now.tzinfo else now.astimezone()

        # ── Framåtblick: negativa och låga priser ────────────────────
        # Kommande 8h (32 kvartstimmar)
        next_8h = [s for s in slots if (s.start - now_aware).total_seconds() <= 8 * 3600]
        # Kommande 2h (8 kvartstimmar) – för proaktiv reaktion
        next_2h = [s for s in slots if (s.start - now_aware).total_seconds() <= 2 * 3600]

        schedule.negative_slots_ahead = sum(
            1 for s in next_8h if s.sell_sek < self.negative_threshold
        )
        schedule.low_price_slots_ahead = sum(
            1 for s in next_8h if s.buy_sek < self.low_price_threshold
        )

        # Proaktiv absorption: om ≥ N negativa slots inom 2h
        negative_within_2h = sum(1 for s in next_2h if s.sell_sek < self.negative_threshold)
        schedule.should_absorb_proactively = (
            negative_within_2h >= self.proactive_absorption_slots
        )

        # Rekommenderad headroom: ju fler negativa slots inom 2h desto mer utrymme
        if negative_within_2h > 0:
            # Max 30% reservkapacitet
            schedule.recommended_headroom = min(0.30, negative_within_2h * 0.05)
        else:
            schedule.recommended_headroom = 0.0

        # ── Kommande 12h ──────────────────────────────────────────────
        next_12h = [s for s in slots if (s.start - now_aware).total_seconds() <= 12 * 3600]
        if next_12h:
            schedule.max_price_next_12h = max(s.buy_sek for s in next_12h)
            schedule.min_price_next_12h = min(s.buy_sek for s in next_12h)
            schedule.best_discharge_slot = max(next_12h, key=lambda s: s.buy_sek)
            schedule.best_charge_slot    = min(next_12h, key=lambda s: s.buy_sek)

        # ── Snittpriser kommande 4h ───────────────────────────────────
        next_4h = [s for s in slots if (s.start - now_aware).total_seconds() <= 4 * 3600]
        if next_4h:
            schedule.avg_price_next_4h = sum(s.buy_sek for s in next_4h) / len(next_4h)

        _LOGGER.debug(
            "PriceScheduler: neg_slots_ahead=%d low_slots=%d proactive=%s headroom=%.0f%%"
            " best_discharge=%.3f best_charge=%.3f",
            schedule.negative_slots_ahead,
            schedule.low_price_slots_ahead,
            schedule.should_absorb_proactively,
            schedule.recommended_headroom * 100,
            schedule.best_discharge_slot.buy_sek if schedule.best_discharge_slot else 0,
            schedule.best_charge_slot.buy_sek if schedule.best_charge_slot else 0,
        )

        return schedule
