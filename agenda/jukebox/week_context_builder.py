import datetime
import logging

from django.utils import timezone
from portion import Interval, closedopen

from agenda.jukebox.scoring import WeekContext
from agenda.jukebox.schedule_repository import ScheduleRepository

__all__ = ["WeekContextBuilder"]

logger = logging.getLogger(__name__)
DEFAULT_LOOKBACK_HOURS = 24


class WeekContextBuilder:
    """Builder for creating WeekContext instances from schedule data.

    Encapsulates the logic for querying existing scheduled items and
    initializing a WeekContext with aggregate information for scoring.
    """

    @staticmethod
    def build_for_window(window: Interval, lookback_hours: int | None = None) -> WeekContext:
        """Build initial week context from existing scheduled items.

        Fetches items within and adjacent to the window to aggregate organization
        airtime and recently played videos for scoring.

        Args:
            start: Window start time
            end: Window end time
            lookback_hours: How far back from ``start`` to include items for context aggregation.
                Defaults to ``DEFAULT_LOOKBACK_HOURS``.

        Returns:
            WeekContext initialized with existing schedule data
        """
        lb_hours = lookback_hours if lookback_hours is not None else DEFAULT_LOOKBACK_HOURS
        lb_window = closedopen(window.lower - datetime.timedelta(hours=lb_hours), window.upper)
        scheduled = ScheduleRepository.fetch_schedule_items_by_interval(lb_window)

        # Create empty context and populate it with existing schedule
        week_ctx = WeekContext(
            total_airtime_for_org={},
            recent_video_ids=[],
            now=timezone.now(),
            proposed_items=[],
        )

        # Add each scheduled item to context
        for si in scheduled:
            if si.video:
                week_ctx.add_proposed_item(si)
        logger.debug(
            "WeekContext: org_airtime_keys=%s, recent_count=%d",
            list(week_ctx.total_airtime_for_org.keys()),
            len(week_ctx.recent_video_ids),
        )
        return week_ctx
