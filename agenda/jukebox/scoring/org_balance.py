import datetime
from dataclasses import dataclass
from typing import List

from agenda.jukebox.program_picker import WeighingResult
from agenda.jukebox.scoring import VideoScorer
from agenda.views import logger
from fk.models import Video, Scheduleitem


@dataclass
class OrganizationBalanceScorer(VideoScorer):
    """Penalize organizations based on their total airtime already scheduled in the week.

    This ensures fair distribution regardless of video length - an organization with
    many short videos won't get more total airtime than one with fewer long videos.
    """

    weight: float = 1.0

    def score(self, video: Video, scheduled_items: List[Scheduleitem], _) -> WeighingResult:
        org_id = getattr(video, "organization_id", None)
        if org_id is None:
            return 0.0
        # Calculate total airtime for this org in scheduled_items
        airtime = datetime.timedelta()
        for item in scheduled_items:
            v = getattr(item, "video", None)
            if v and getattr(v, "organization_id", None) == org_id:
                airtime += getattr(item, "duration", datetime.timedelta())
        airtime_minutes = airtime.total_seconds() / 60.0
        # Fewer minutes already scheduled -> higher score
        # Using 1/(1+minutes) gives a smooth decay curve
        score = self.weight * (1.0 / (1.0 + airtime_minutes))
        logger.debug(
            "OrganizationBalanceScorer(video=%s, org=%s): airtime_min=%.1f -> score=%.4f (w=%.2f)",
            getattr(video, "id", None),
            org_id,
            airtime_minutes,
            score,
            self.weight,
        )
        return WeighingResult(criteria_name=self.__name__, score=score)
