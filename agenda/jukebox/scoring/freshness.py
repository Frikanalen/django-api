import datetime
from dataclasses import dataclass
from typing import List

from agenda.jukebox.dataclasses import WeighingResult
from agenda.jukebox.scoring.protocol import VideoScorer
from agenda.views import logger
from fk.models import Video, Scheduleitem


@dataclass
class FreshnessScorer(VideoScorer):
    """Prefer newer videos.

    Assumes Video has 'id' and 'created_time' fields.
    """

    weight: float = 1.0

    def score(
        self,
        video: Video,
        scheduled_items: List[Scheduleitem],
        now: datetime.datetime,
    ) -> WeighingResult:
        # Newer gets higher score in [0, 1]
        age_seconds = max((now - video.created_time).total_seconds(), 0.0)
        # Normalize with a 30-day half-life
        half_life = 30 * 24 * 3600
        freshness = 1.0 / (1.0 + age_seconds / half_life)
        logger.debug(
            "FreshnessScorer(video=%s): age_seconds=%.0f -> freshness=%.4f (w=%.2f)",
            video.id,
            age_seconds,
            freshness,
            self.weight,
        )
        return WeighingResult(criteria_name=self.__class__.__name__, score=self.weight * freshness)
