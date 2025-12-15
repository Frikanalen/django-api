from typing import Iterable, List, Optional
import datetime

from portion import Interval

from agenda.jukebox.jukebox_planner import get_default_scorer
from agenda.jukebox.scoring import CompositeScorer, WeekContext, GapContext
from agenda.jukebox.utils import ceil_5minute
from agenda.views import logger
from fk.models import Video, Scheduleitem

from agenda.jukebox.program_picker import ProgramPicker

__all__ = ["GapFiller"]


class GapFiller:
    def __init__(self, candidates: Iterable[Video], scorer: Optional[CompositeScorer] = None):
        """Prepare a filler that selects videos from candidates to fill a given gap.

        Selection uses highest score among non-overshooting candidates.
        """
        self._candidates: List[Video] = list(candidates)
        self._picker = ProgramPicker(scorer or get_default_scorer())

    def get_items_for_gap(
        self,
        gap: Interval,
        week_ctx: WeekContext,
    ) -> List[Scheduleitem]:
        """Fill a single gap by placing videos without overshooting.

        Selects videos from available_videos based on scoring. Videos are added to
        week_ctx.recent_video_ids as they are placed to prevent the same video from
        being selected multiple times within this gap or in subsequent gaps.

        Args:
            gap: Time interval to fill
            week_ctx: Mutable context tracking placed videos and org airtime (updated in-place)

        Returns:
            List of unsaved Scheduleitem objects for this gap
        """
        gap_cursor: datetime.datetime = gap.lower
        new_items: List[Scheduleitem] = []
        gap_ctx = GapContext(gap=gap)

        logger.info(
            "Filling jukebox from %s to %s - %d available",
            gap.lower,
            gap.upper,
            len(self._candidates),
        )
        logger.debug(
            "Gap duration minutes: %.1f",
            (gap.upper - gap.lower).total_seconds() / 60.0,
        )

        while gap_cursor < gap.upper:
            fitting = [v for v in self._candidates if gap_cursor + v.duration <= gap.upper]
            if len(fitting) == 0:
                logger.info("No videos fit timeslot %s-%s", gap_cursor, gap.upper)
                break

            best_video = self._picker.pick(
                gap_cursor=gap_cursor,
                gap_end=gap.upper,
                fitting=fitting,
                week_ctx=week_ctx,
                gap_ctx=gap_ctx,
            )

            item = Scheduleitem(
                video=best_video,
                schedulereason=Scheduleitem.REASON_JUKEBOX,
                starttime=gap_cursor,
                duration=best_video.duration,
            )
            new_items.append(item)
            logger.info("Added video %s at %s", best_video.id, gap_cursor.strftime("%H:%M:%S"))

            # Record in week context (proposed, not persisted)
            week_ctx.add_proposed_item(item)

            gap_cursor = ceil_5minute(gap_cursor + best_video.duration)

        return new_items
