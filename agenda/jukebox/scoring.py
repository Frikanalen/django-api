import datetime
from dataclasses import dataclass
from typing import Dict, List, Protocol

import portion as P

from fk.models import Video


@dataclass
class WeekContext:
    """Context for scoring within an ISO week window.

    Holds window bounds and aggregate information useful for scoring.
    This is a mutable shared context that accumulates state as videos are placed.
    """

    start: datetime.datetime
    end: datetime.datetime
    # Aggregates for diversity, cooling, etc. (mutable, updated as gaps are filled)
    org_counts: Dict[int, int]  # organization_id -> count in week
    recent_video_ids: List[int]  # recently scheduled ids (cooling)
    now: datetime.datetime


@dataclass(frozen=True)
class GapContext:
    gap: P.Interval


class VideoScorer(Protocol):
    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float: ...


@dataclass
class FreshnessScorer:
    """Prefer newer videos.

    Assumes Video has a 'created' or 'published' field; falls back to id.
    """

    weight: float = 1.0

    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float:
        # Try common freshness fields; default to 0 if unavailable
        date = getattr(video, "published", None) or getattr(video, "created", None)
        if isinstance(date, datetime.datetime):
            # Newer gets higher score in [0, 1]
            age_seconds = max((week_ctx.now - date).total_seconds(), 0.0)
            # Normalize with a 30-day half-life
            half_life = 30 * 24 * 3600
            freshness = 1.0 / (1.0 + age_seconds / half_life)
        else:
            # As a weak proxy, prefer higher IDs
            freshness = float(getattr(video, "id", 0)) % 1000 / 1000.0
        return self.weight * freshness


@dataclass
class OrganizationBalanceScorer:
    """Penalize organizations that already dominate in the week.

    Assumes Video has organization_id.
    """

    weight: float = 1.0

    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float:
        org_id = getattr(video, "organization_id", None)
        if org_id is None:
            return 0.0
        count = week_ctx.org_counts.get(int(org_id), 0)
        # Fewer already scheduled -> higher score
        return self.weight * (1.0 / (1.0 + float(count)))


@dataclass
class CoolingPeriodScorer:
    """Strongly penalize videos that appear in recent_video_ids."""

    weight: float = 1.0

    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float:
        vid_id = getattr(video, "id", None)
        if vid_id is None:
            return 0.0
        if int(vid_id) in week_ctx.recent_video_ids:
            return -self.weight  # hard penalty
        return 0.0


@dataclass
class CompositeScorer:
    """Combine multiple scorers by weighted sum."""

    scorers: List[VideoScorer]

    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float:
        return sum(s.score(video, week_ctx, gap_ctx) for s in self.scorers)
