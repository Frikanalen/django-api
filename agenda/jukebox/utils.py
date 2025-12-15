"""
Utility functions for jukebox scheduling.
"""

import datetime

from django.utils import timezone


def ceil_minute(dt: datetime.datetime) -> datetime.datetime:
    """Round datetime up to the next minute boundary."""
    return floor_minute(dt) + datetime.timedelta(minutes=1)


def ceil_5minute(dt: datetime.datetime) -> datetime.datetime:
    """Round datetime up to the next 5-minute boundary (00, 05, 10, etc.)."""
    floored = floor_minute(dt)
    minute = floored.minute
    # Calculate how many minutes to add to get to next 5-minute mark
    minutes_to_add = (5 - minute % 5) % 5
    if minutes_to_add == 0 and (dt.second > 0 or dt.microsecond > 0):
        # If already on 5-minute boundary but has seconds/microseconds, round up
        minutes_to_add = 5
    return floored + datetime.timedelta(minutes=minutes_to_add)


def floor_minute(dt: datetime.datetime) -> datetime.datetime:
    """Round datetime down to the current minute boundary (removes seconds and microseconds)."""
    return dt.replace(second=0, microsecond=0)


def get_week_boundaries(
    iso_year: int, iso_week: int, tz=None
) -> tuple[datetime.datetime, datetime.datetime]:
    """Calculate start and end datetimes for an ISO week.

    ISO week starts on Monday (weekday=1).

    Args:
        iso_year: ISO year
        iso_week: ISO week number (1-53)
        tz: Timezone (defaults to current timezone)

    Returns:
        Tuple of (start, end) datetimes for the week
    """
    tz = tz or timezone.get_current_timezone()
    week_start_date = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    start = datetime.datetime.combine(week_start_date, datetime.time.min)
    start = timezone.make_aware(start, tz)
    end = start + datetime.timedelta(days=7)
    return start, end
