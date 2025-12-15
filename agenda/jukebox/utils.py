"""
Utility functions for jukebox scheduling.
"""

import datetime

from django.utils import timezone
from portion import Interval, closedopen


def ceil_5minute(dt: datetime.datetime) -> datetime.datetime:
    """Round datetime up to the next 5-minute boundary (00, 05, 10, etc.)."""
    floored = dt.replace(second=0, microsecond=0)
    minute = floored.minute
    # Calculate how many minutes to add to get to next 5-minute mark
    minutes_to_add = (5 - minute % 5) % 5
    if minutes_to_add == 0 and (dt.second > 0 or dt.microsecond > 0):
        # If already on 5-minute boundary but has seconds/microseconds, round up
        minutes_to_add = 5
    return floored + datetime.timedelta(minutes=minutes_to_add)


def week_as_interval(iso_year: int, iso_week: int) -> Interval:
    """
    Calculate the interval corresponding to a specific ISO year and week.

    This function takes an ISO year and an ISO week number, computes the start
    date and time of the week, and returns the interval covering the specified
    ISO week. The start time is included, while the end time is exclusive.
    If no timezone is provided, the system's current timezone is used.

    Args:
        iso_year (int): The ISO year.
        iso_week (int): The ISO week number.

    Returns:
        Interval: A closed-open interval representing the start and exclusive
                  end of the specified ISO week.
    """
    week_start_date = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    start = timezone.make_aware(datetime.datetime.combine(week_start_date, datetime.time.min))
    end = start + datetime.timedelta(days=7)
    return closedopen(start, end)
