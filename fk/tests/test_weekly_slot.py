"""
WeeklySlot's date arithmetic decides *when* the automatic scheduler
places content. Pure computations, no database needed.
"""

from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from fk.models import WeeklySlot

MONDAY = 0
FRIDAY = 4

# 2015-01-05 is a Monday.
A_MONDAY = date(2015, 1, 5)


def slot(day: int = MONDAY, start: time = time(12, 0), **fields) -> WeeklySlot:
    return WeeklySlot(
        day=day,
        start_time=start,
        duration=fields.pop("duration", timedelta(hours=1)),
        **fields,
    )


def test_end_time_adds_the_duration() -> None:
    assert slot(start=time(12, 30), duration=timedelta(hours=2)).end_time == time(14, 30)


def test_end_time_without_duration_is_the_start_time() -> None:
    assert slot(start=time(12, 30), duration=timedelta(0)).end_time == time(12, 30)


@pytest.mark.parametrize(
    ("slot_day", "from_date", "expected"),
    [
        # Later this week.
        pytest.param(FRIDAY, A_MONDAY, date(2015, 1, 9), id="upcoming-weekday"),
        # Earlier in the week: pushed to next week.
        pytest.param(MONDAY, date(2015, 1, 9), date(2015, 1, 12), id="already-passed"),
        # Quirk pinned on purpose: a slot on the from-date's own weekday
        # also goes to *next* week, so 'today's' slot is never chosen.
        pytest.param(MONDAY, A_MONDAY, date(2015, 1, 12), id="same-day-skips-a-week"),
    ],
)
def test_next_date(slot_day: int, from_date: date, expected: date) -> None:
    assert slot(day=slot_day).next_date(from_date) == expected


def test_next_datetime_is_aware_in_the_django_timezone() -> None:
    result = slot(day=FRIDAY, start=time(12, 30)).next_datetime(A_MONDAY)

    assert result.date() == date(2015, 1, 9)
    assert result.timetz().replace(tzinfo=None) == time(12, 30)
    assert result.tzinfo is not None
    assert result.utcoffset() == ZoneInfo("Europe/Oslo").utcoffset(result.replace(tzinfo=None))
