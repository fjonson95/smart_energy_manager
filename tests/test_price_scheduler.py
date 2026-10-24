from datetime import datetime, timedelta, timezone

from custom_components.smart_energy_manager.price_scheduler import PriceScheduler

TZ = timezone(timedelta(hours=2))


def _nordpool_attrs(day: datetime, spot_at_hour) -> dict:
    raw = []
    for q in range(96):
        start = day + timedelta(minutes=15 * q)
        raw.append({
            "start": start.isoformat(),
            "end": (start + timedelta(minutes=15)).isoformat(),
            "value": spot_at_hour(start.hour),
        })
    return {"raw_today": raw, "raw_tomorrow": [], "price_in_cents": False}


def test_negative_slots_passed_today_counts_slots_earlier_today():
    day = datetime(2026, 6, 14, 0, 0, tzinfo=TZ)
    attrs = _nordpool_attrs(day, lambda h: -0.10 if 11 <= h < 13 else 0.50)
    now = day.replace(hour=16)

    schedule = PriceScheduler().compute(attrs, now)

    assert schedule.negative_slots_passed_today == 8
    assert schedule.negative_slots_ahead == 0
    assert all(s.end > now for s in schedule.slots)


def test_negative_slots_passed_today_excludes_future_and_running_slot():
    day = datetime(2026, 6, 14, 0, 0, tzinfo=TZ)
    attrs = _nordpool_attrs(day, lambda h: -0.10 if 11 <= h < 13 else 0.50)
    now = day.replace(hour=11, minute=20)

    schedule = PriceScheduler().compute(attrs, now)

    # 11:00 och 11:15 har startat; 11:30–12:45 ligger framför oss.
    assert schedule.negative_slots_passed_today == 2
    assert schedule.negative_slots_ahead == 7
