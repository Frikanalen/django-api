import datetime
from typing import Iterable, List, Optional, Dict, Any
from dataclasses import dataclass

from django.utils import timezone
from portion import Interval

from agenda.jukebox.gap_filler import GapFiller
from agenda.jukebox.gap_finder import GapFinder
from agenda.jukebox.utils import ceil_minute, floor_minute
from agenda.jukebox.scoring import (
    WeekContext,
    FreshnessScorer,
    OrganizationBalanceScorer,
    CoolingPeriodScorer,
    CompositeScorer,
)
from fk.models import Video, Scheduleitem


# --- Public API -----------------------------------------------------------------


def fill_with_jukebox(start: Optional[datetime.datetime] = None, days: int = 1):
    """Fill the schedule with jukebox videos for the given time window.

    This is the stable entrypoint used by tests and callers. Internally this
    delegates to a small planner class that cleanly separates finding gaps and
    filling them with candidate videos.
    """
    start = start or timezone.now()
    end = start + datetime.timedelta(days=days)

    candidates = Video.objects.fillers().order_by("?")

    planner = JukeboxPlanner(candidates)
    jukebox_choices = planner.items_for_gap(start, end)

    # Create all scheduleitems in bulk to minimize DB writes
    Scheduleitem.objects.bulk_create(
        [
            Scheduleitem(
                video=schedobj["video"],
                schedulereason=Scheduleitem.REASON_JUKEBOX,
                starttime=schedobj["starttime"],
                duration=schedobj["video"].duration,
            )
            for schedobj in jukebox_choices
        ]
    )

    return jukebox_choices


# --- Week view ------------------------------------------------------------------


@dataclass(frozen=True)
class ReservedSlot:
    start: datetime.datetime
    end: datetime.datetime
    source: Scheduleitem


@dataclass(frozen=True)
class WeekView:
    start: datetime.datetime
    end: datetime.datetime
    free_slots_list: List[Interval]

    def free_slots(self) -> List[Interval]:
        return self.free_slots_list


class WeekBuilder:
    def build_iso_week(self, iso_year: int, iso_week: int, tz=None) -> WeekView:
        tz = tz or timezone.get_current_timezone()
        # ISO week starts Monday (weekday=1)
        week_start_date = datetime.date.fromisocalendar(iso_year, iso_week, 1)
        start = datetime.datetime.combine(week_start_date, datetime.time.min)
        start = timezone.make_aware(start, tz)
        end = start + datetime.timedelta(days=7)

        # Compute reserved intervals from existing Scheduleitems overlapping window
        startdt, enddt = Scheduleitem.objects.expand_to_surrounding(start, end)  # type: ignore[attr-defined]
        scheduled = list(
            Scheduleitem.objects.filter(starttime__gte=startdt, starttime__lte=enddt).order_by(
                "starttime"
            )
        )
        reserved_slots: List[ReservedSlot] = [
            ReservedSlot(si.starttime, si.endtime(), si) for si in scheduled
        ]

        # Derive free slots by subtracting reserved intervals from [start, end)
        intervals = sorted(reserved_slots, key=lambda r: r.start)
        pointer = ceil_minute(start)
        window_end = floor_minute(end)
        free_slots: List[Interval] = []
        for r in intervals:
            if r.end <= pointer:
                continue
            if r.start > window_end:
                break
            potential_end = floor_minute(r.start)
            gap_seconds = (potential_end - pointer).total_seconds()
            if gap_seconds > GapFinder.MINIMUM_GAP_SECONDS:
                free_slots.append(Interval(pointer, potential_end))
            pointer = ceil_minute(r.end)
            if pointer >= window_end:
                break
        if pointer < window_end:
            gap_seconds = (window_end - pointer).total_seconds()
            if gap_seconds > GapFinder.MINIMUM_GAP_SECONDS:
                free_slots.append(Interval(pointer, window_end))

        return WeekView(start=start, end=end, free_slots_list=free_slots)


# --- Planner ---------------------------------------------------------------------


class JukeboxPlanner:
    """Encapsulates the logic for detecting schedule gaps and filling them.

    Future concerns (recency, representation, diversity) can be plugged in as
    separate scorer/selector classes consumed by this planner without changing
    the public API.
    """

    def __init__(self, candidates: Iterable[Video], scorer: Optional[CompositeScorer] = None):
        # Default scorer combining basic strategies
        default_scorer = CompositeScorer(
            scorers=[
                FreshnessScorer(weight=1.0),
                OrganizationBalanceScorer(weight=1.0),
                CoolingPeriodScorer(weight=2.0),
            ]
        )
        self._scorer = scorer or default_scorer
        self._filler = GapFiller(candidates, scorer=self._scorer)
        self._finder = GapFinder()

    def _build_week_context(self, start: datetime.datetime, end: datetime.datetime) -> WeekContext:
        # Aggregate org counts and recent videos from schedule within [start, end)
        startdt, enddt = Scheduleitem.objects.expand_to_surrounding(start, end)  # type: ignore[attr-defined]
        scheduled = list(
            Scheduleitem.objects.filter(starttime__gte=startdt, starttime__lte=enddt).order_by(
                "starttime"
            )
        )
        org_counts: Dict[int, int] = {}
        recent_video_ids: List[int] = []
        for si in scheduled:
            vid = si.video
            if hasattr(vid, "organization_id") and vid.organization_id is not None:
                org_counts[int(vid.organization_id)] = (
                    org_counts.get(int(vid.organization_id), 0) + 1
                )
            if hasattr(vid, "id") and vid.id is not None:
                recent_video_ids.append(int(vid.id))
        return WeekContext(
            start=start,
            end=end,
            org_counts=org_counts,
            recent_video_ids=recent_video_ids,
            now=timezone.now(),
        )

    def items_for_gap(
        self, start: datetime.datetime, end: datetime.datetime
    ) -> List[Dict[str, Any]]:
        gaps = self._finder.find_gaps(start, end)
        week_ctx = self._build_week_context(start, end)
        full_items: List[Dict[str, Any]] = []
        available_videos = list(self._filler._candidates)

        for gap in gaps:
            items = self._filler.fill_gap(gap, available_videos, week_ctx)

            # Update org_counts and recent list as we place items, so scoring adapts
            for schedobj in items:
                vid = schedobj["video"]
                if hasattr(vid, "organization_id") and vid.organization_id is not None:
                    org_id = int(vid.organization_id)
                    week_ctx.org_counts[org_id] = week_ctx.org_counts.get(org_id, 0) + 1
                if hasattr(vid, "id") and vid.id is not None:
                    week_ctx.recent_video_ids.append(int(vid.id))

            full_items.extend(items)

        return full_items

    def plan_iso_week(self, iso_year: int, iso_week: int, tz=None) -> List[Dict[str, Any]]:
        """Plan and persist jukebox placements for a whole ISO week.

        Returns a list of placement dicts similar to items_for_gap output.
        """
        week = WeekBuilder().build_iso_week(iso_year, iso_week, tz=tz)
        week_ctx = self._build_week_context(week.start, week.end)
        full_items: List[Dict[str, Any]] = []
        available_videos = list(self._filler._candidates)

        for gap in week.free_slots():
            items = self._filler.fill_gap(gap, available_videos, week_ctx)

            # Persist like fill_with_jukebox using bulk_create per slot
            Scheduleitem.objects.bulk_create(
                [
                    Scheduleitem(
                        video=schedobj["video"],
                        schedulereason=Scheduleitem.REASON_JUKEBOX,
                        starttime=schedobj["starttime"],
                        duration=schedobj["video"].duration,
                    )
                    for schedobj in items
                ]
            )

            # Update org_counts and recent list as we place items, so scoring adapts
            for schedobj in items:
                vid = schedobj["video"]
                if hasattr(vid, "organization_id") and vid.organization_id is not None:
                    org_id = int(vid.organization_id)
                    week_ctx.org_counts[org_id] = week_ctx.org_counts.get(org_id, 0) + 1
                if hasattr(vid, "id") and vid.id is not None:
                    week_ctx.recent_video_ids.append(int(vid.id))

            full_items.extend(items)

        return full_items
