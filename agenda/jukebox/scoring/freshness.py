import datetime
from dataclasses import dataclass
from typing import List

from agenda.jukebox.picker import WeighingResult
from agenda.jukebox.scoring import VideoScorer
from agenda.views import logger
from fk.models import Video, Scheduleitem


@dataclass
class FreshnessScorer(VideoScorer):
    """Prefer newer videos.

    Assumes Video has a 'created' or 'published' field; falls back to id.
    """

    weight: float = 1.0

    def score(
        self,
        video: Video,
        scheduled_items: List[Scheduleitem],
        now: datetime.datetime,
    ) -> WeighingResult:
        # Try common freshness fields; default to 0 if unavailable
        date = getattr(video, "published", None) or getattr(video, "created", None)
        if isinstance(date, datetime.datetime):
            # Newer gets higher score in [0, 1]
            age_seconds = max((now - date).total_seconds(), 0.0)
            # Normalize with a 30-day half-life
            half_life = 30 * 24 * 3600
            freshness = 1.0 / (1.0 + age_seconds / half_life)
            logger.debug(
                "FreshnessScorer(video=%s): age_seconds=%.0f -> freshness=%.4f (w=%.2f)",
                getattr(video, "id", None),
                age_seconds,
                freshness,
                self.weight,
            )
        else:
            # As a weak proxy, prefer higher IDs
            freshness = float(getattr(video, "id", 0)) % 1000 / 1000.0
            logger.debug(
                "FreshnessScorer(video=%s): proxy freshness=%.4f (w=%.2f)",
                getattr(video, "id", None),
                freshness,
                self.weight,
            )
        return WeighingResult(criteria_name=self.__name__, score=self.weight * freshness)
