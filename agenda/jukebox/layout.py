import datetime
from typing import Iterable, List

from portion import Interval, closedopen

from agenda.jukebox.utils import round_up_to_5min_boundary, interval_duration_sec
from agenda.views import logger
from fk.models import Video, Scheduleitem

from agenda.jukebox.picker import ProgramPicker


class ScheduleLayout:
    @staticmethod
    def _scheduleitem_from_pick(video: Video, start_time: datetime.datetime) -> Scheduleitem:
        return Scheduleitem(
            video=video,
            schedulereason=Scheduleitem.REASON_JUKEBOX,
            starttime=start_time,
            duration=video.duration,
        )

    def __init__(self, candidates: Iterable[Video] = None, picker: ProgramPicker = None):
        self._candidates: List[Video] = candidates or Video.objects.fillers()
        self._picker = picker or ProgramPicker()

    def layout(
        self,
        gap: Interval,
        scheduled_items: List[Scheduleitem],
        now,
    ) -> List[Scheduleitem]:
        """Fill a single gap by placing videos without overshooting.

        Selects videos from available_videos based on scoring. Videos are added to
        scheduled_items as they are placed to prevent the same video from
        being selected multiple times within this gap or in subsequent gaps.

        Args:
            gap: Time interval to fill
            scheduled_items: List of scheduled and proposed items
            now: Current datetime

        Returns:
            List of unsaved Scheduleitem objects for this gap
        """
        new_items: List[Scheduleitem] = []

        logger.info("Filling gap %s - %d available", gap, len(self._candidates))
        logger.debug("Gap duration minutes: %.1f", interval_duration_sec(gap) / 60.0)

        gap_cursor = gap.lower
        while gap_cursor < gap.upper:
            candidates = [v for v in self._candidates if gap_cursor + v.duration <= gap.upper]
            moving_window = closedopen(gap_cursor, gap.upper)

            if len(candidates) == 0:
                logger.info("No videos fit timeslot %s", moving_window)
                break

            video = self._picker.pick(
                window=moving_window,
                candidates=candidates,
                current_schedule=scheduled_items + new_items,
                now=now,
            )

            item = self._scheduleitem_from_pick(video=video, start_time=gap_cursor)
            new_items.append(item)
            logger.info("Added schedule item %s", item)
            gap_cursor = round_up_to_5min_boundary(gap_cursor + item.duration)

        return new_items
