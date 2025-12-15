from typing import List
from dataclasses import dataclass

from portion import Interval

from agenda.jukebox.scoring import RANKING_CRITERIA
from agenda.jukebox.pprint import render_candidates_table
from agenda.views import logger
from fk.models import Video, Scheduleitem


@dataclass(frozen=True)
class WeighingResult:
    criteria_name: str
    score: float


@dataclass(frozen=True)
class ScoredCandidate:
    video: Video
    total: float
    weights: List[WeighingResult]


class ProgramPicker:
    """Encapsulates selection of the best-fitting program (video) for a given gap cursor.

    Responsible solely for choosing a candidate using scoring and fit constraints.
    """

    @staticmethod
    def _score_candidate(
        video: Video,
        scheduled_items: List[Scheduleitem],
        now,
    ) -> ScoredCandidate:
        """Return a ScoredCandidate dataclass for a given candidate."""
        component_rankings = [c.scorer.score(video, scheduled_items, now) for c in RANKING_CRITERIA]
        component_rankings.sort(key=lambda x: x.score, reverse=True)
        total = sum(x.score for x in component_rankings)
        return ScoredCandidate(video=video, total=total, weights=component_rankings)

    def pick(
        self,
        window: Interval,
        candidates: List[Video],
        current_schedule: List[Scheduleitem],
        now,
    ) -> Video:
        scored = [self._score_candidate(v, current_schedule, now) for v in candidates]
        render_candidates_table(scored, window, candidates)
        best = scored[0]
        logger.info("Placed %s at %s with score %.4f", best.video.id, window.lower, best.total)
        return best.video
