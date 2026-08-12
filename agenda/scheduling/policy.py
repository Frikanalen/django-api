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

Besides the clock boundaries, this module also owns the displacement
rule -- what may be scheduled over: automatic jukebox filler gives way,
deliberate programming never does. The weekly-slot filler and the
schedule API both apply it from here.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from fk.models import Scheduleitem

FROZEN_WEEKS = 2  # the current week and the next
DRAFTED_WEEKS = 3  # ...plus the open week the nightly jobs keep filled

# The policy is defined in this zone, not the request's or the
# process's: fkweb.middleware overrides Django's active timezone to UTC
# for every /api/ request, so anchoring on timezone.localtime here
# would put the API's idea of Monday midnight an hour or two off the
# cron jobs'.
TZ = ZoneInfo("Europe/Oslo")


def week_start(moment: datetime) -> datetime:
    """Monday 00:00, Europe/Oslo, of the broadcast week containing `moment`."""
    local = moment.astimezone(TZ)
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
    open_from = boundary.astimezone(TZ).date().isoformat()
    return f"The schedule is frozen before {open_from}; only the week starting then is open."


def _weeks_after_week_start(now: datetime | None, weeks: int) -> datetime:
    local = (now or timezone.now()).astimezone(TZ)
    monday = local.date() - timedelta(days=local.weekday())
    return _local_midnight(monday + timedelta(weeks=weeks))


def _local_midnight(day: date) -> datetime:
    # Built from the wall-clock date and only then localized, so a week
    # containing a DST transition still ends on a true local midnight.
    return datetime.combine(day, time.min, tzinfo=TZ)


def is_displaceable(item: Scheduleitem) -> bool:
    """Whether a placement may be scheduled over `item`.

    Only automatic jukebox filler gives way; deliberate programming --
    slot placements, member picks, admin entries -- never does.
    """
    return item.schedulereason == Scheduleitem.REASON_JUKEBOX


def airtime_conflicts(
    start: datetime,
    end: datetime,
    exclude_pk: int | None = None,
    for_update: bool = False,
) -> tuple[list[Scheduleitem], list[Scheduleitem]]:
    """What stands in the way of placing something on [start, end).

    Returns (blocking, displaceable), each ordered by starttime: items
    that refuse the placement, and jukebox fillers that give way to it.
    `for_update` locks the conflict rows for the enclosing transaction,
    so concurrent displacements of the same fillers serialize.
    """
    conflicts = Scheduleitem.objects.overlapping(start, end).order_by("starttime")
    if exclude_pk is not None:
        conflicts = conflicts.exclude(pk=exclude_pk)
    if for_update:
        conflicts = conflicts.select_for_update()
    items = list(conflicts)
    blocking = [item for item in items if not is_displaceable(item)]
    displaceable = [item for item in items if is_displaceable(item)]
    return blocking, displaceable


def displace(fillers: list[Scheduleitem]) -> None:
    """Delete jukebox fillers that a placement is scheduling over. The
    nightly jukebox repacks whatever slivers this leaves behind."""
    if fillers:
        Scheduleitem.objects.filter(pk__in=[item.pk for item in fillers]).delete()
