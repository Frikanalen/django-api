import datetime
from dataclasses import dataclass
from typing import Dict, List, Protocol

from portion import Interval

from agenda.views import logger
from fk.models import Video, Scheduleitem


@dataclass
class WeekContext:
    """Context for scoring within an ISO week window.

    Holds window bounds and aggregate information useful for scoring.
    This is a mutable shared context that accumulates state as videos are placed.
    """

    start: datetime.datetime
    end: datetime.datetime
    # Aggregates for diversity, cooling, etc. (mutable, updated as gaps are filled)
    total_airtime_for_org: Dict[int, datetime.timedelta]  # organization_id -> total airtime in week
    recent_video_ids: List[int]  # recently scheduled ids (cooling)
    now: datetime.datetime
    # Proposed schedule items (unsaved) for this planning session
    proposed_items: List[Scheduleitem]  # accumulated as items are proposed

    def add_proposed_item(self, item: Scheduleitem) -> None:
        """Append a proposed Scheduleitem and update aggregate stats.

        This keeps a list of items proposed (not persisted) so that callers can
        inspect or adjust later, while we still maintain incremental aggregates
        for efficient scoring decisions.
        """
        if item is None:
            return
        # Track item
        self.proposed_items.append(item)

        video = getattr(item, "video", None)
        duration = getattr(item, "duration", None) or datetime.timedelta()

        # Add to recent videos for cooling period
        if video and hasattr(video, "id") and video.id is not None:
            self.recent_video_ids.append(int(video.id))

        # Update org airtime for balance scoring
        if video and hasattr(video, "organization_id") and video.organization_id is not None:
            org_id = int(video.organization_id)
            current_airtime = self.total_airtime_for_org.get(org_id, datetime.timedelta())
            self.total_airtime_for_org[org_id] = current_airtime + duration

    def add_placed_item(self, video: Video, duration: datetime.timedelta) -> None:
        """Backward-compatible: update context after placing a video.

        Delegates to add_proposed_item by constructing a temporary Scheduleitem-like
        holder so existing callers continue to work.
        """
        # Minimal shim object to reuse add_proposed_item logic
        item = Scheduleitem(video=video, duration=duration, starttime=self.now)
        self.add_proposed_item(item)


@dataclass(frozen=True)
class GapContext:
    gap: Interval


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
        return self.weight * freshness


@dataclass
class OrganizationBalanceScorer:
    """Penalize organizations based on their total airtime already scheduled in the week.

    This ensures fair distribution regardless of video length - an organization with
    many short videos won't get more total airtime than one with fewer long videos.
    """

    weight: float = 1.0

    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float:
        org_id = getattr(video, "organization_id", None)
        if org_id is None:
            return 0.0
        airtime = week_ctx.total_airtime_for_org.get(int(org_id), datetime.timedelta())
        # Convert to minutes for scoring
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
        return score


@dataclass
class CoolingPeriodScorer:
    """Boost videos that are NOT in recent_video_ids (freshness diversity).

    Returns weight for non-recent videos, 0 for recent ones.
    """

    weight: float = 1.0

    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float:
        vid_id = getattr(video, "id", None)
        if vid_id is None:
            return 0.0
        if int(vid_id) in week_ctx.recent_video_ids:
            # Recent video: no boost (0), so it won't win over non-recent candidates
            logger.debug(
                "CoolingPeriodScorer(video=%s): in recent -> score=0.0000 (no boost; w=%.2f)",
                vid_id,
                self.weight,
            )
            return 0.0
        # Non-recent video: boost it
        logger.debug(
            "CoolingPeriodScorer(video=%s): not recent -> boost=%.4f (w=%.2f; hard_exclude=%s)",
            vid_id,
            self.weight,
            self.weight,
        )
        return self.weight


@dataclass
class CompositeScorer:
    """Combine multiple scorers by weighted sum."""

    scorers: List[VideoScorer]

    def score(self, video: Video, week_ctx: WeekContext, gap_ctx: GapContext) -> float:
        total = 0.0
        for s in self.scorers:
            val = s.score(video, week_ctx, gap_ctx)
            total += val
        logger.debug(
            "CompositeScorer(video=%s): total=%.4f",
            getattr(video, "id", None),
            total,
        )
        return total
