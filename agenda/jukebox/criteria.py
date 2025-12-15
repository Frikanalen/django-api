from dataclasses import dataclass
from typing import List

from agenda.jukebox.scoring.protocol import VideoScorer
from agenda.jukebox.scoring.freshness import FreshnessScorer
from agenda.jukebox.scoring.org_balance import OrganizationBalanceScorer
from agenda.jukebox.scoring.cooling_period import CoolingPeriod


@dataclass(frozen=True)
class RankingCriterion:
    name: str
    scorer: VideoScorer


RANKING_CRITERIA: List[RankingCriterion] = [
    RankingCriterion("freshness", FreshnessScorer(weight=1.0)),
    RankingCriterion("org_balance", OrganizationBalanceScorer(weight=1.0)),
    RankingCriterion("cooling_period", CoolingPeriod(weight=2.0)),
]
