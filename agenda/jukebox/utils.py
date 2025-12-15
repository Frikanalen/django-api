"""
Utility functions for jukebox scheduling.
"""

import datetime


def ceil_minute(dt: datetime.datetime) -> datetime.datetime:
    """Round datetime up to the next minute boundary."""
    return floor_minute(dt) + datetime.timedelta(minutes=1)


def floor_minute(dt: datetime.datetime) -> datetime.datetime:
    """Round datetime down to the current minute boundary (removes seconds and microseconds)."""
    return dt.replace(second=0, microsecond=0)
