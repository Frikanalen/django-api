from typing import Iterable

from agenda.jukebox.scoring import (
    CompositeScorer,
    FreshnessScorer,
    OrganizationBalanceScorer,
    CoolingPeriodScorer,
)

from fk.models import Video


def get_default_scorer() -> CompositeScorer:
    return CompositeScorer(
        scorers=[
            FreshnessScorer(weight=1.0),
            OrganizationBalanceScorer(weight=1.0),
            CoolingPeriodScorer(weight=2.0),
        ]
    )


class JukeboxPlanner:
    """Encapsulates the logic for detecting schedule gaps and filling them.

    Future concerns (recency, representation, diversity) can be plugged in as
    separate scorer/selector classes consumed by this planner without changing
    the public API.
    """

    def __init__(self, candidates: Iterable[Video]):
        from agenda.jukebox.gap_filler import GapFiller

        self._filler = GapFiller(
            candidates,
            get_default_scorer(),
        )
