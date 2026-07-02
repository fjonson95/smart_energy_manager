"""Prisplanering baserad på Nordpool kvartstimmespriser och Solcast-prognos.

Läser raw_today / raw_tomorrow från Nordpool-sensorn och detailedForecast
från Solcast-sensorerna och beräknar:
  - Om kommande perioder har negativa/låga priser → proaktiv absorption
  - Bästa urladdningstimmar (höga priser framåt)
  - Bästa laddningstimmar (låga priser framåt, för vinterläge)
  - Antal kvartstimmar med negativt pris framåt
  - Sol-prognos per slot och aggregat (2h, 4h)
  - Om det lönar sig att vänta på sol snarare än ladda från nät

Nordpool-sensorn rapporterar i öre/kWh → vi dividerar med 100 för SEK/kWh.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

_LOGGER = logging.getLogger(__name__)

OERE_TO_SEK = 0.01   # öre/kWh → SEK/kWh


@dataclass
class SolarForecastSlot:
    """30-minuters solprognos från Solcast."""
    start: datetime
    kw_estimate: float     # median (pv_estimate)
    kw_estimate10: float   # pessimistisk (p10)
    kw_estimate90: float   # optimistisk (p90)

    @property
    def kwh_estimate(self) -> float:
        """Energi i kWh för 30-minutersintervallet."""
        return self.kw_estimate * 0.5


@dataclass
class PriceSlot:
    """Ett kvartstimme-prisintervall med valfri solprognos."""
    start: datetime
    end: datetime
    spot_sek: float          # SEK/kWh (konverterat från öre)
    buy_sek: float = 0.0     # Köppris inkl avgifter/skatt/moms
    sell_sek: float = 0.0    # Säljpris
    solar_kw: float = 0.0    # Förväntad soleffekt under slotten (kW, median)
    solar_kwh: float = 0.0   # Förväntad solenergi under slotten (kWh)

    @property
    def net_buy_sek(self) -> float:
        """Effektivt köppris med hänsyn till gratis solenergi.

        Används för att prioritera bort gridladdning när sol är på väg.
        Om solar_kwh >= 0.5 kWh (täcker hela slotten för ett normalt hem)
        är nettokostnaden noll.
        """
        return max(0.0, self.buy_sek - self.solar_kw * self.buy_sek * 0.5)


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
    recommended_headroom: float = 0.0

    # ── Solprognos-fält ───────────────────────────────────────────────

    # Förväntad solenergi kommande 2h (kWh, median)
    solar_next_2h_kwh: float = 0.0

    # Förväntad solenergi kommande 4h (kWh, median)
    solar_next_4h_kwh: float = 0.0

    # Förväntad solenergi kommande 8h (kWh, median)
    solar_next_8h_kwh: float = 0.0

    # Bästa soltimmen kommande 8h (max solar_kw i en slot)
    peak_solar_kw_next_8h: float = 0.0

    # Tidpunkt för soltoppen kommande 8h
    peak_solar_time: Optional[datetime] = None

    # Om det lönar sig att vänta på sol snarare än ladda från nät nu.
    # Sant om: sol_nästa_2h > 2 kWh OCH aktuellt köppris > 0.50 SEK/kWh
    should_wait_for_solar: bool = False

    # Timmar tills soltoppen nås (0 om det är nu eller ingen prognos)
    hours_to_solar_peak: float = 0.0


class PriceScheduler:
    """
    Analyserar Nordpool kvartstimmespriser och ger styrningsrekommendationer.
    Kan även ta emot Solcast-prognos för att berika per-slot data.

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
        proactive_absorption_slots: int = 4,
        wait_for_solar_threshold_kwh: float = 2.0,   # kWh sol inom 2h för att vänta
        wait_for_solar_min_price_sek: float = 0.50,  # Köppris över detta → vänta på sol
    ):
        self.grid_fees = grid_fees_sek
        self.energy_tax = energy_tax_sek
        self.vat = vat_rate
        self.extra_revenue = extra_revenue_sek
        self.negative_threshold = negative_threshold_sek
        self.low_price_threshold = low_price_threshold_sek
        self.proactive_absorption_slots = proactive_absorption_slots
        self.wait_for_solar_threshold_kwh = wait_for_solar_threshold_kwh
        self.wait_for_solar_min_price_sek = wait_for_solar_min_price_sek

    def _buy_price(self, spot_sek: float) -> float:
        return (spot_sek + self.grid_fees + self.energy_tax) * (1 + self.vat)

    def _sell_price(self, spot_sek: float) -> float:
        return spot_sek + self.extra_revenue

    def parse_solcast_attributes(
        self,
        today_attrs: Optional[dict],
        tomorrow_attrs: Optional[dict],
        now: datetime,
    ) -> list[SolarForecastSlot]:
        """
        Parsa detailedForecast från Solcast-sensorernas attribut.

        Solcast ger pv_estimate i kW per 30-minutersintervall.
        Returnerar lista av SolarForecastSlot framåt i tid.
        """
        slots: list[SolarForecastSlot] = []
        now_aware = now if now.tzinfo else now.astimezone()

        for attrs in filter(None, [today_attrs, tomorrow_attrs]):
            detailed = attrs.get("detailedForecast", [])
            if not detailed:
                continue
            for entry in detailed:
                try:
                    if hasattr(entry, "get"):
                        start_raw = entry.get("period_start")
                        kw = float(entry.get("pv_estimate", 0) or 0)
                        kw10 = float(entry.get("pv_estimate10", 0) or 0)
                        kw90 = float(entry.get("pv_estimate90", 0) or 0)
                    else:
                        start_raw = entry["period_start"]
                        kw = float(entry.get("pv_estimate", 0) or 0)
                        kw10 = float(entry.get("pv_estimate10", 0) or 0)
                        kw90 = float(entry.get("pv_estimate90", 0) or 0)

                    if start_raw is None:
                        continue

                    if isinstance(start_raw, datetime):
                        start = start_raw
                    else:
                        start = datetime.fromisoformat(str(start_raw))

                    if start.tzinfo is None:
                        start = start.astimezone()

                    # Solcast-slots är 30 min; vi inkluderar pågående och framtida
                    slot_end = start + timedelta(minutes=30)
                    if slot_end <= now_aware:
                        continue

                    slots.append(SolarForecastSlot(
                        start=start,
                        kw_estimate=kw,
                        kw_estimate10=kw10,
                        kw_estimate90=kw90,
                    ))
                except (KeyError, ValueError, TypeError, AttributeError) as e:
                    _LOGGER.debug("Kunde inte parsa Solcast-slot %s: %s", entry, e)

        slots.sort(key=lambda s: s.start)
        _LOGGER.debug("PriceScheduler: parsade %d Solcast-slots", len(slots))
        return slots

    def _match_solar_to_slots(
        self,
        price_slots: list[PriceSlot],
        solar_slots: list[SolarForecastSlot],
    ) -> None:
        """
        Matcha Solcast 30-minutersslottar mot Nordpool-slots (också 30 min).
        Uppdaterar solar_kw/solar_kwh direkt på PriceSlot-objekten.
        """
        if not solar_slots:
            return

        for ps in price_slots:
            # Hitta solslottar som överlappar prisslotten
            total_kw = 0.0
            count = 0
            for ss in solar_slots:
                ss_end = ss.start + timedelta(minutes=30)
                if ss.start < ps.end and ss_end > ps.start:
                    total_kw += ss.kw_estimate
                    count += 1
            if count:
                ps.solar_kw = total_kw / count
                ps.solar_kwh = ps.solar_kw * 0.5

    def parse_nordpool_attributes(self, attributes: dict, now: datetime) -> list[PriceSlot]:
        """
        Parsa raw_today och raw_tomorrow från Nordpool-attributen.
        Returnerar sorterad lista av PriceSlot framåt i tid.
        """
        slots: list[PriceSlot] = []
        now_aware = now if now.tzinfo else now.astimezone()

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
                    start_raw = entry.get("start") if hasattr(entry, "get") else entry["start"]
                    end_raw   = entry.get("end")   if hasattr(entry, "get") else entry["end"]
                    value     = entry.get("value") if hasattr(entry, "get") else entry["value"]

                    if start_raw is None or end_raw is None or value is None:
                        continue

                    if isinstance(start_raw, datetime):
                        start = start_raw
                    else:
                        start = datetime.fromisoformat(str(start_raw))

                    if isinstance(end_raw, datetime):
                        end = end_raw
                    else:
                        end = datetime.fromisoformat(str(end_raw))

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

        _LOGGER.debug("PriceScheduler: parsade %d Nordpool-slots totalt", parsed_count)

        if not slots:
            _LOGGER.warning(
                "PriceScheduler: inga slots parsades – kontrollera att Nordpool-sensorn "
                "har attributen raw_today/raw_tomorrow. Tillgängliga attribut: %s",
                list(attributes.keys()),
            )
            return []

        slots.sort(key=lambda s: s.start)
        future = [s for s in slots if s.end > now_aware]
        _LOGGER.debug(
            "PriceScheduler: %d slots framåt (av %d totalt), nu=%s",
            len(future), len(slots), now_aware.isoformat()
        )
        return future

    def compute(
        self,
        nordpool_attributes: dict,
        now: datetime,
        solcast_today_attrs: Optional[dict] = None,
        solcast_tomorrow_attrs: Optional[dict] = None,
    ) -> PriceSchedule:
        """
        Beräkna komplett prisschema och rekommendationer.

        Anropas av koordinatorn varje cykel.
        """
        schedule = PriceSchedule()
        slots = self.parse_nordpool_attributes(nordpool_attributes, now)
        schedule.slots = slots

        if not slots:
            _LOGGER.debug("PriceScheduler: inga slots tillgängliga")
            return schedule

        now_aware = now if now.tzinfo else now.astimezone()

        # ── Solcast-prognos ───────────────────────────────────────────
        if solcast_today_attrs or solcast_tomorrow_attrs:
            solar_slots = self.parse_solcast_attributes(
                solcast_today_attrs, solcast_tomorrow_attrs, now
            )
            self._match_solar_to_slots(slots, solar_slots)

            # Aggregat per tidshorisont
            for hours, attr in [(2, "solar_next_2h_kwh"), (4, "solar_next_4h_kwh"), (8, "solar_next_8h_kwh")]:
                window = [
                    ss for ss in solar_slots
                    if (ss.start - now_aware).total_seconds() <= hours * 3600
                ]
                setattr(schedule, attr, sum(ss.kwh_estimate for ss in window))

            # Soltopp kommande 8h
            next_8h_solar = [
                ss for ss in solar_slots
                if (ss.start - now_aware).total_seconds() <= 8 * 3600
            ]
            if next_8h_solar:
                peak = max(next_8h_solar, key=lambda s: s.kw_estimate)
                schedule.peak_solar_kw_next_8h = peak.kw_estimate
                schedule.peak_solar_time = peak.start
                delta_h = (peak.start - now_aware).total_seconds() / 3600
                schedule.hours_to_solar_peak = max(0.0, delta_h)

            _LOGGER.debug(
                "PriceScheduler: sol 2h=%.2f kWh 4h=%.2f kWh 8h=%.2f kWh topp=%.1f kW om %.1fh",
                schedule.solar_next_2h_kwh,
                schedule.solar_next_4h_kwh,
                schedule.solar_next_8h_kwh,
                schedule.peak_solar_kw_next_8h,
                schedule.hours_to_solar_peak,
            )

        # ── Framåtblick: negativa och låga priser ────────────────────
        next_8h = [s for s in slots if (s.start - now_aware).total_seconds() <= 8 * 3600]
        next_2h = [s for s in slots if (s.start - now_aware).total_seconds() <= 2 * 3600]

        schedule.negative_slots_ahead = sum(
            1 for s in next_8h if s.sell_sek < self.negative_threshold
        )
        schedule.low_price_slots_ahead = sum(
            1 for s in next_8h if s.buy_sek < self.low_price_threshold
        )

        negative_within_2h = sum(1 for s in next_2h if s.sell_sek < self.negative_threshold)
        schedule.should_absorb_proactively = (
            negative_within_2h >= self.proactive_absorption_slots
        )

        if negative_within_2h > 0:
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

        # ── Vänta på sol? ─────────────────────────────────────────────
        # Sant om: rimlig sol väntas inom 2h OCH elpriset är tillräckligt högt
        # för att motivera att inte ladda från nät just nu.
        current_buy = slots[0].buy_sek if slots else 0.0
        schedule.should_wait_for_solar = (
            schedule.solar_next_2h_kwh >= self.wait_for_solar_threshold_kwh
            and current_buy >= self.wait_for_solar_min_price_sek
        )

        _LOGGER.debug(
            "PriceScheduler: neg_slots=%d low_slots=%d proactive=%s headroom=%.0f%%"
            " best_discharge=%.3f best_charge=%.3f wait_for_solar=%s",
            schedule.negative_slots_ahead,
            schedule.low_price_slots_ahead,
            schedule.should_absorb_proactively,
            schedule.recommended_headroom * 100,
            schedule.best_discharge_slot.buy_sek if schedule.best_discharge_slot else 0,
            schedule.best_charge_slot.buy_sek if schedule.best_charge_slot else 0,
            schedule.should_wait_for_solar,
        )

        return schedule
