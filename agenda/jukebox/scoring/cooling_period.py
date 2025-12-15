import datetime
from dataclasses import dataclass
from typing import List

from agenda.jukebox.picker import WeighingResult
from agenda.jukebox.scoring import VideoScorer
from agenda.views import logger
from fk.models import Video, Scheduleitem


@dataclass
class CoolingPeriod(VideoScorer):
    """Boost videos that are NOT in recent_video_ids (freshness diversity).

    Returns weight for non-recent videos, 0 for recent ones.
    """

    weight: float = 1.0
    recent_count: int = 10  # How many recent videos to consider for cooling

    def score(
        self,
        video: Video,
        scheduled_items: List[Scheduleitem],
        now: datetime.datetime,
    ) -> WeighingResult:
        vid_id = getattr(video, "id", None)
        if vid_id is None:
            return 0.0
        # Get recent video ids from the last N scheduled items
        recent_video_ids = [
            getattr(item.video, "id", None) for item in scheduled_items[-self.recent_count :]
        ]
        if int(vid_id) in recent_video_ids:
            # Recent video: no boost (0), so it won't win over non-recent candidates
            logger.debug(
                "CoolingPeriodScorer(video=%s): in recent -> score=0.0000 (no boost; w=%.2f)",
                vid_id,
                self.weight,
            )
            return 0.0
        # Non-recent video: boost it
        logger.debug(
            "CoolingPeriodScorer(video=%s): not recent -> boost=%.4f (w=%.2f; hard_exclude=%s)",
            vid_id,
            self.weight,
            self.weight,
        )
        return WeighingResult(criteria_name=self.__name__, score=self.weight)
