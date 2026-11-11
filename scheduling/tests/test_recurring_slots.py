"""Recurrence rules: which days a slot comes up on, and at what instant.

Unsaved RecurringSlot instances -- no database.
"""

import datetime
from datetime import UTC, date, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from scheduling.models import RecurringSlot

OSLO = ZoneInfo("Europe/Oslo")
Freq = RecurringSlot.Frequency


def slot(**fields) -> RecurringSlot:
    fields.setdefault("start_time", time(19))
    fields.setdefault("duration", timedelta(hours=1))
    fields.setdefault("starts_on", date(2026, 1, 1))  # a Thursday
    return RecurringSlot(**fields)


def days(recurring: RecurringSlot, start: date, end: date) -> list[date]:
    out = []
    day = start
    while day <= end:
        if recurring.occurs_on(day):
            out.append(day)
        day += timedelta(days=1)
    return out


JANUARY = (date(2026, 1, 1), date(2026, 1, 31))


def test_daily() -> None:
    assert len(days(slot(frequency=Freq.DAILY), *JANUARY)) == 31


def test_weekdays_only() -> None:
    weekdays = days(slot(frequency=Freq.DAILY, weekdays=[0, 1, 2, 3, 4]), *JANUARY)
    assert len(weekdays) == 22
    assert all(day.weekday() < 5 for day in weekdays)


def test_weekly() -> None:
    assert days(slot(frequency=Freq.WEEKLY, weekdays=[3]), *JANUARY) == [
        date(2026, 1, 1),
        date(2026, 1, 8),
        date(2026, 1, 15),
        date(2026, 1, 22),
        date(2026, 1, 29),
    ]


def test_fortnightly_counts_weeks_from_starts_on() -> None:
    fortnightly = slot(frequency=Freq.WEEKLY, interval=2, weekdays=[0, 3])
    assert days(fortnightly, *JANUARY) == [
        date(2026, 1, 1),  # Thursday of the starting week (its Monday is before starts_on)
        date(2026, 1, 12),
        date(2026, 1, 15),
        date(2026, 1, 26),
        date(2026, 1, 29),
    ]


def test_first_thursday_of_the_month() -> None:
    monthly = slot(frequency=Freq.MONTHLY, weekdays=[3], month_weeks=[1])
    assert days(monthly, date(2026, 1, 1), date(2026, 4, 30)) == [
        date(2026, 1, 1),
        date(2026, 2, 5),
        date(2026, 3, 5),
        date(2026, 4, 2),
    ]


def test_first_and_third_thursday() -> None:
    monthly = slot(frequency=Freq.MONTHLY, weekdays=[3], month_weeks=[1, 3])
    assert days(monthly, *JANUARY) == [date(2026, 1, 1), date(2026, 1, 15)]


def test_last_friday_of_the_month() -> None:
    monthly = slot(frequency=Freq.MONTHLY, weekdays=[4], month_weeks=[-1])
    assert days(monthly, date(2026, 1, 1), date(2026, 2, 28)) == [
        date(2026, 1, 30),
        date(2026, 2, 27),
    ]


def test_every_other_month() -> None:
    monthly = slot(frequency=Freq.MONTHLY, interval=2, weekdays=[3], month_weeks=[1])
    assert days(monthly, date(2026, 1, 1), date(2026, 6, 30)) == [
        date(2026, 1, 1),
        date(2026, 3, 5),
        date(2026, 5, 7),
    ]


def test_starts_on_and_ends_on_bound_the_rule() -> None:
    bounded = slot(
        frequency=Freq.WEEKLY,
        weekdays=[3],
        starts_on=date(2026, 1, 8),
        ends_on=date(2026, 1, 22),
    )
    assert days(bounded, *JANUARY) == [date(2026, 1, 8), date(2026, 1, 15), date(2026, 1, 22)]


def test_occurrences_are_oslo_wall_clock_instants_across_dst() -> None:
    weekly = slot(frequency=Freq.WEEKLY, weekdays=[6], start_time=time(13))
    start = datetime.datetime(2026, 3, 21, tzinfo=OSLO)
    end = datetime.datetime(2026, 4, 2, tzinfo=OSLO)

    instants = list(weekly.occurrences(start, end))

    # 2026-03-29 is the spring transition; both are 13:00 in Oslo.
    assert instants == [
        datetime.datetime(2026, 3, 22, 12, tzinfo=UTC),
        datetime.datetime(2026, 3, 29, 11, tzinfo=UTC),
    ]


def test_occurrences_are_half_open() -> None:
    weekly = slot(frequency=Freq.WEEKLY, weekdays=[3])
    at = datetime.datetime(2026, 1, 8, 19, tzinfo=OSLO)

    assert list(weekly.occurrences(at, at + timedelta(minutes=1))) == [at]
    assert list(weekly.occurrences(at - timedelta(days=1), at)) == []


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"frequency": Freq.WEEKLY}, "weekdays"),
        ({"frequency": Freq.MONTHLY, "weekdays": [3]}, "month_weeks"),
        ({"frequency": Freq.WEEKLY, "weekdays": [3], "month_weeks": [1]}, "month_weeks"),
    ],
)
def test_clean_requires_a_complete_rule(fields: dict, field: str) -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        slot(**fields).clean()
    assert field in excinfo.value.error_dict


def test_describe() -> None:
    assert slot(frequency=Freq.WEEKLY, weekdays=[3]).describe() == "Weekly Thu"
    assert (
        slot(frequency=Freq.MONTHLY, weekdays=[3], month_weeks=[1, 3]).describe()
        == "Monthly (1, 3) Thu"
    )
