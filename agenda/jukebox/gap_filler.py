from typing import Iterable, List, Dict, Any

import portion as P

from agenda.jukebox.scoring import CompositeScorer, WeekContext, GapContext
from agenda.jukebox.utils import ceil_minute
from agenda.views import logger
from fk.models import Video


class GapFiller:
    def __init__(self, candidates: Iterable[Video], scorer: CompositeScorer):
        """Prepare a filler that selects videos from candidates to fill a given gap.

        Selection uses highest score among non-overshooting candidates.
        """
        self._candidates: List[Video] = list(candidates)
        self._scorer = scorer

    def fill_gap(
        self,
        gap: P.Interval,
        available_videos: List[Video],
        week_ctx: WeekContext,
    ) -> List[Dict[str, Any]]:
        """Fill a single gap by placing videos without overshooting.

        Returns a list of placement dicts. Videos placed are removed from available_videos.
        """
        start, end = gap.lower, gap.upper
        current_time = start
        new_items: List[Dict[str, Any]] = []

        logger.info(
            "Filling jukebox from %s to %s - %d available", start, end, len(available_videos)
        )

        while current_time < end:
            # Filter to videos that fit without overshooting
            fitting: List[Video] = [v for v in available_videos if current_time + v.duration <= end]  # type: ignore[operator]
            if not fitting:
                logger.debug("No fitting videos for time %s", current_time)
                break

            gap_ctx = GapContext(gap=gap)
            # Pick highest score
            best_video = max(fitting, key=lambda v: self._scorer.score(v, week_ctx, gap_ctx))

            # Remove from available pool
            available_videos.remove(best_video)

            new_items.append({"id": best_video.id, "starttime": current_time, "video": best_video})
            logger.info("Added video %s at %s", best_video.id, current_time.strftime("%H:%M:%S"))
            current_time = ceil_minute(current_time + best_video.duration)  # type: ignore[operator]

        return new_items
