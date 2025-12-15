import datetime
from dataclasses import dataclass

from typing import List, Protocol

from agenda.jukebox.picker import WeighingResult

from agenda.jukebox.scoring.cooling_period import CoolingPeriod
from agenda.jukebox.scoring.freshness import FreshnessScorer
from agenda.jukebox.scoring.org_balance import OrganizationBalanceScorer

from fk.models import Video, Scheduleitem

__all__ = [
    "CoolingPeriod",
    "FreshnessScorer",
    "OrganizationBalanceScorer",
    "VideoScorer",
    "RANKING_CRITERIA",
]


class VideoScorer(Protocol):
    def score(
        self,
        video: Video,
        scheduled_items: List[Scheduleitem],
        now: datetime.datetime,
    ) -> WeighingResult: ...


@dataclass(frozen=True)
class RankingCriterion:
    name: str
    scorer: VideoScorer


RANKING_CRITERIA: List[RankingCriterion] = [
    RankingCriterion("freshness", FreshnessScorer(weight=1.0)),
    RankingCriterion("org_balance", OrganizationBalanceScorer(weight=1.0)),
    RankingCriterion("cooling_period", CoolingPeriod(weight=2.0)),
]
