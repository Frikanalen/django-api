from typing import List, Tuple

from portion import Interval

from agenda.jukebox.scoring import CompositeScorer, WeekContext, GapContext
from agenda.jukebox.pprint import render_candidates_table
from agenda.views import logger
from fk.models import Video


class ProgramPicker:
    """Encapsulates selection of the best-fitting program (video) for a given gap cursor.

    Responsible solely for choosing a candidate using scoring and fit constraints.
    """

    def __init__(self, scorer: CompositeScorer):
        self._scorer = scorer

    def _score_candidate(
        self,
        video: Video,
        week_ctx: WeekContext,
        gap_ctx: GapContext,
    ) -> Tuple[Video, float, List[Tuple[str, float]]]:
        per: List[Tuple[str, float]] = []
        total = 0.0
        for s in self._scorer.scorers:
            try:
                val = s.score(video, week_ctx, gap_ctx)
            except Exception as e:
                logger.exception("Scorer %s failed on video %s: %s", type(s).__name__, video.id, e)
                val = 0.0
            per.append((type(s).__name__, val))
            total += val
        return video, total, per

    def _sort(
        self, scored: List[Tuple[Video, float, List[Tuple[str, float]]]]
    ) -> List[Tuple[Video, float, List[Tuple[str, float]]]]:
        return sorted(scored, key=lambda t: t[1], reverse=True)

    def pick(
        self,
        window: Interval,
        candidates: List[Video],
        week_ctx: WeekContext,
        gap_ctx: GapContext,
    ) -> Video:
        scored = self._sort([self._score_candidate(v, week_ctx, gap_ctx) for v in candidates])
        render_candidates_table(
            scored_candidates=scored,
            scorer_names=[type(s).__name__ for s in (self._scorer.scorers)],
            window=window,
            total_fitting=len(candidates),
        )
        best_video = scored[0][0]
        logger.info("Placed %s at %s with score %.4f", best_video.id, window.lower, scored[0][1])
        return best_video
