"""Broadcast time: the one place wall-clock times become instants.

Everything about scheduling that is phrased in wall-clock terms -- "every
Thursday at 19:00", "the Monday before its week airs" -- is defined in
Europe/Oslo, whatever the active Django timezone is (fkweb.middleware
forces UTC for every /api/ request). Grants store instants, so once a
grant is issued nothing converts wall-clock times again.

The broadcast week runs Monday 00:00 to the next Monday 00:00, Oslo.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

TZ = ZoneInfo("Europe/Oslo")

# Grants are issued through the end of the week after next, so every
# week is issued two Mondays before it airs and locks the Monday before.
ISSUED_WEEKS = 3


def local_instant(day: date, at: time) -> datetime:
    """The instant `at` happens on `day` in Oslo, as an aware UTC datetime.

    DST is resolved explicitly rather than left to arithmetic:
    a wall-clock time that does not exist (02:30 on the spring-forward
    night) moves forward by the gap, to 03:30 summer time; one that
    happens twice (02:30 on the fall-back night) means the first of the
    two, in summer time.
    """
    # zoneinfo resolves both cases this way with fold=0: a time in the
    # gap takes the offset from before the transition, which lands it
    # the gap's length later once in summer time; an ambiguous time
    # takes the earlier, summer-time offset. Tested, not assumed.
    return datetime.combine(day, at, tzinfo=TZ).astimezone(UTC)


def local_midnight(day: date) -> datetime:
    return local_instant(day, time.min)


def local_date(moment: datetime) -> date:
    return moment.astimezone(TZ).date()


def week_start(moment: datetime) -> datetime:
    """Monday 00:00, Oslo, of the broadcast week containing `moment`."""
    day = local_date(moment)
    return local_midnight(day - timedelta(days=day.weekday()))


def weeks_after(moment: datetime, weeks: int) -> datetime:
    """Monday 00:00, Oslo, `weeks` weeks after the week containing `moment`.

    Computed on dates, not by adding timedeltas to an instant, so a week
    containing a DST transition still ends on a true local midnight.
    """
    day = local_date(moment)
    monday = day - timedelta(days=day.weekday())
    return local_midnight(monday + timedelta(weeks=weeks))


def default_locks_at(starttime: datetime) -> datetime:
    """The Monday 00:00 before the week `starttime` airs in: 7-13 days ahead."""
    return weeks_after(starttime, -1)


def issue_horizon(now: datetime | None = None) -> datetime:
    """How far ahead grants are issued: the end of the week after next."""
    return weeks_after(now or timezone.now(), ISSUED_WEEKS)
