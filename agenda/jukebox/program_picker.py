import datetime
from typing import List, Tuple

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

    def pick(
        self,
        gap_cursor: datetime.datetime,
        gap_end: datetime.datetime,
        fitting: List[Video],
        week_ctx: WeekContext,
        gap_ctx: GapContext,
    ) -> Video:
        # Compute total and per-scorer contributions for diagnostics
        scored: List[Tuple[Video, float, List[Tuple[str, float]]]] = []
        for v in fitting:
            per: List[Tuple[str, float]] = []
            total = 0.0
            for s in self._scorer.scorers:
                try:
                    val = s.score(v, week_ctx, gap_ctx)
                except Exception as e:
                    logger.exception(
                        "Scorer %s failed on video %s: %s",
                        type(s).__name__,
                        getattr(v, "id", None),
                        e,
                    )
                    val = 0.0
                per.append((type(s).__name__, val))
                total += val

            scored.append((v, total, per))

        # Sort by total score descending (rely on scorers to encode penalties/exclusions)
        scored.sort(key=lambda t: t[1], reverse=True)

        top_n = min(5, len(scored))
        logger.info(
            "Gap %s -> %s: %d fitting candidates",
            gap_cursor,
            gap_end,
            len(fitting),
        )

        # Render Rich table for readability
        render_candidates_table(
            scored_candidates=scored[:top_n],
            scorer_names=[type(s).__name__ for s in (self._scorer.scorers)],
            gap_start=gap_cursor,
            gap_end=gap_end,
            total_fitting=len(fitting),
            top_n=top_n,
        )

        best_video = scored[0][0]
        video_score = scored[0][1]
        logger.info("Placed %s at %s with score %.4f", best_video.id, gap_cursor, video_score)
        return best_video
