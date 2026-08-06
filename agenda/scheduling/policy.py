"""When the broadcast schedule is drafted, opened, and frozen.

The unit of scheduling is the broadcast week: Monday 00:00 to the next
Monday 00:00, Europe/Oslo. Every week has the same lifecycle, which is
the whole policy and fits in three sentences for members:

* Two Mondays before it airs, the nightly jobs draft the week: weekly
  slots place their videos and the jukebox fills the rest.
* For the following week, member organizations may adjust it through
  the API -- their picks displace jukebox fillers.
* From the Monday before it airs, the week is frozen: members can no
  longer change it. (Staff can, and the jukebox may still fill airtime
  that is genuinely empty -- dead air is worse than a late change.)

So at any moment the current and next broadcast weeks are frozen, the
week after next is open, and later weeks are not yet drafted.
"""

from datetime import date, datetime, time, timedelta

from django.utils import timezone

FROZEN_WEEKS = 2  # the current week and the next
DRAFTED_WEEKS = 3  # ...plus the open week the nightly jobs keep filled


def week_start(moment: datetime) -> datetime:
    """Monday 00:00, local time, of the broadcast week containing `moment`."""
    local = timezone.localtime(moment)
    monday = local.date() - timedelta(days=local.weekday())
    return _local_midnight(monday)


def freeze_boundary(now: datetime | None = None) -> datetime:
    """Items starting before this moment are frozen to members."""
    return _weeks_after_week_start(now, FROZEN_WEEKS)


def scheduling_horizon(now: datetime | None = None) -> datetime:
    """How far ahead the nightly drafting jobs fill: end of the open week."""
    return _weeks_after_week_start(now, DRAFTED_WEEKS)


def is_frozen(starttime: datetime, now: datetime | None = None) -> bool:
    return starttime < freeze_boundary(now)


def freeze_message(boundary: datetime | None = None) -> str:
    """The refusal members see, stating when the open week starts."""
    boundary = boundary or freeze_boundary()
    open_from = timezone.localtime(boundary).date().isoformat()
    return f"The schedule is frozen before {open_from}; only the week starting then is open."


def _weeks_after_week_start(now: datetime | None, weeks: int) -> datetime:
    local = timezone.localtime(now or timezone.now())
    monday = local.date() - timedelta(days=local.weekday())
    return _local_midnight(monday + timedelta(weeks=weeks))


def _local_midnight(day: date) -> datetime:
    # Built from the wall-clock date and only then localized, so a week
    # containing a DST transition still ends on a true local midnight.
    return timezone.make_aware(datetime.combine(day, time.min))
