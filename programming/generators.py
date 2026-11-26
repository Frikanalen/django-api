"""The kinds of recipe: where each finds its candidates and how it
picks, and the one packing loop.

A kind is two small functions in a registry. `candidates` says which
videos the recipe may use; `selector` returns "the next video no longer
than the time left". :func:`plan` puts the owner's pinned videos first
and packs the rest of the window with whatever the selector hands it.

Planning is a pure function of the recipe, its candidates, the window,
what is pinned and the shared ScheduleContext, so a run can recompute
every automatic grant and only write when the answer changed. Nothing
here knows about grants or slots: a window is anything with a `pk` (to
seed a repeatable draw) and a `duration`.
"""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from django.db.models import Count, Q, QuerySet
from django.db.models.functions import Coalesce

from fk.models import Scheduleitem, Video
from programming.models import Recipe
from programming.selection import ScheduleContext, Selector, WeightedSelector, default_rules


class Window(Protocol):
    pk: int
    starttime: datetime
    endtime: datetime

    @property
    def duration(self) -> timedelta: ...

    @property
    def padded(self) -> bool: ...


# -- Candidates: which videos a recipe may use --------------------------------
#
# A recipe only picks published videos whose file is processed
# (``airable``). A pending upload or an unpublished video may still be
# listed by hand, but nothing schedules it automatically.


def _uploads(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    return Video.objects.airable().filter(organization=recipe.organization_id)


def _series(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    if not hasattr(recipe, "series_source"):  # the series was deleted
        return _uploads(recipe, now)
    return Video.objects.airable().filter(series=recipe.series_source.series_id)


def _playlist(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    if not hasattr(recipe, "playlist_source"):
        return Video.objects.none()
    # Finished files only: a list is hand-picked from what exists.
    listed = recipe.playlist_source.videos.values("pk")
    return Video.objects.airable().filter(pk__in=listed)


# The jukebox pool: every finished, published member video without TONO
# music. No longer limited to is_filler, which in practice only very old
# videos carry, so the jukebox leaned hard on the archive. Freshness in
# the weighted draw now does the steering instead.
JUKEBOX_POOL = Q(
    has_tono_records=False,
    organization__fkmember=True,
    proper_import=True,
    publish_on_web=True,
)


def _jukebox(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    videos = Video.objects.listable().filter(JUKEBOX_POOL)
    settings = getattr(recipe, "jukebox_settings", None)
    if settings is None:
        return videos
    categories = list(settings.categories.values_list("pk", flat=True))
    if categories:
        videos = videos.filter(categories__in=categories)
    if settings.shortest is not None:
        videos = videos.filter(duration__gte=settings.shortest)
    if settings.longest is not None:
        videos = videos.filter(duration__lte=settings.longest)
    if settings.newer_than_days or settings.older_than_days:
        videos = videos.annotate(arrived=Coalesce("uploaded_time", "created_time"))
        if settings.newer_than_days:
            videos = videos.filter(arrived__gte=now - timedelta(days=settings.newer_than_days))
        if settings.older_than_days:
            videos = videos.filter(arrived__lte=now - timedelta(days=settings.older_than_days))
    return videos


def candidates(recipe: Recipe, now: datetime) -> list[Video]:
    """What the recipe may pick from, newest first. Never a zero-length
    video. The owner's own uploads and series may include a pending
    upload -- an entry made ahead of its file -- so a weekly regular can
    create next week's entry now and upload later."""
    videos = KINDS[recipe.kind].candidates(recipe, now)
    return list(videos.distinct().order_by("-created_time", "-id"))


# -- Selectors: how the next one is chosen ------------------------------------


class InOrder:
    """Hands out candidates in a fixed order, each at most once per window.
    A candidate too long for the time left keeps its place."""

    def __init__(
        self, ordered: Sequence[Video], context: ScheduleContext, at: datetime | None = None
    ) -> None:
        self._remaining = list(ordered)
        self._context = context
        self._at = at

    def pick(self, remaining: timedelta) -> Video | None:
        for index, video in enumerate(self._remaining):
            if video.duration <= remaining:
                del self._remaining[index]
                self._context.record(video, at=self._at)
                return video
        return None


def _times_aired(pool: Sequence[Video], context: ScheduleContext) -> Callable[[Video], int]:
    """How often each video is on the air: aired and compiled schedule
    items, plus every grant item the run has counted. What the grants
    being refilled compiled earlier is left out: the run decides that
    again."""
    scheduled = dict(
        Video.objects.filter(pk__in=[v.pk for v in pool])
        .annotate(n=Count("scheduleitem", filter=~Q(scheduleitem__grant__in=context.refilling)))
        .values_list("pk", "n")
    )

    def times(video: Video) -> int:
        uncompiled = context.times_played[video.pk] - context.compiled_plays[video.pk]
        return scheduled.get(video.pk, 0) + uncompiled

    return times


def premiere(pool, window, context, now) -> Selector:
    """What has never been on the air, newest first; then the archive,
    least aired first, the older one breaking a tie. A new upload leads
    the next window and then takes its place in the rotation, so a slot
    does not repeat itself weekly while it waits for the next one."""
    times = _times_aired(pool, context)
    unaired = [v for v in pool if times(v) == 0]  # the pool is newest first
    archive = sorted(
        (v for v in pool if times(v) > 0), key=lambda v: (times(v), v.created_time, v.pk)
    )
    return InOrder([*unaired, *archive], context, at=window.starttime)


def weighted(pool, window, context, now) -> Selector:
    """The weighted draw from :mod:`programming.selection`, seeded by the
    window so it repeats as long as nothing real changed."""
    return WeightedSelector(
        pool, context, default_rules(now=now), rng=random.Random(window.pk), at=window.starttime
    )


# New material: a video uploaded in the last NEW_FOR airs about twice a
# week. The weighted draw alone cannot promise that, so the jukebox
# first hands out any new video that has not aired within REPEAT_GAP of
# the window, and only then draws from the older material.
NEW_FOR = timedelta(days=14)
REPEAT_GAP = timedelta(days=3)


class NewMaterialFirst:
    """Due new videos first, the fewest-aired and newest leading; then
    whatever `then` picks. A video is due in a window when it was
    uploaded within NEW_FOR before the window starts and does not air
    within REPEAT_GAP either side of it, counting what has aired, what
    is scheduled, and what this run has picked so far."""

    def __init__(
        self, pool, window, context: ScheduleContext, then: Selector, now: datetime
    ) -> None:
        self._at = window.starttime
        self._context = context
        self._then = then
        self._new = [
            v
            for v in pool
            if v.uploaded_time is not None and self._at - NEW_FOR <= v.uploaded_time <= self._at
        ]
        # What aired before the run; the context holds what comes after.
        self._aired: dict[int, list[datetime]] = {}
        for video_id, start in Scheduleitem.objects.filter(
            video__in=[v.pk for v in self._new],
            starttime__gte=self._at - NEW_FOR - REPEAT_GAP,
            starttime__lt=min(now, self._at),
        ).values_list("video_id", "starttime"):
            self._aired.setdefault(video_id, []).append(start)

    def _airings(self, video: Video) -> list[datetime]:
        return self._aired.get(video.pk, []) + self._context.airings[video.pk]

    def _due(self, video: Video) -> bool:
        return all(abs(self._at - at) >= REPEAT_GAP for at in self._airings(video))

    def pick(self, remaining: timedelta) -> Video | None:
        due = [v for v in self._new if v.duration <= remaining and self._due(v)]
        if not due:
            return self._then.pick(remaining)
        video = min(due, key=lambda v: (len(self._airings(v)), -v.uploaded_time.timestamp()))
        self._context.record(video, at=self._at)
        return video


def jukebox(pool, window, context, now) -> Selector:
    """New material on its quota, then the weighted draw over the rest.
    New videos air only on the quota, so they come round about every
    REPEAT_GAP rather than whenever the draw favours them."""
    new = {v.pk for v in pool if _is_new(v, window.starttime)}
    rest = [v for v in pool if v.pk not in new]
    return NewMaterialFirst(pool, window, context, weighted(rest, window, context, now), now)


def _is_new(video: Video, at: datetime) -> bool:
    return video.uploaded_time is not None and at - NEW_FOR <= video.uploaded_time <= at


# -- The kinds ----------------------------------------------------------------


@dataclass(frozen=True)
class KindSpec:
    candidates: Callable[[Recipe, datetime], QuerySet[Video]]
    selector: Callable[..., Selector]


KINDS: dict[str, KindSpec] = {
    Recipe.Kind.UPLOADS: KindSpec(_uploads, premiere),
    Recipe.Kind.SERIES: KindSpec(_series, premiere),
    Recipe.Kind.PLAYLIST: KindSpec(_playlist, premiere),
    Recipe.Kind.JUKEBOX: KindSpec(_jukebox, jukebox),
}


# -- The packing loop ---------------------------------------------------------


def plan(
    recipe: Recipe,
    window: Window,
    context: ScheduleContext,
    now: datetime,
    pinned: Sequence = (),
) -> list:
    """What `window` lists under `recipe`: the `pinned` entries its owner
    chose (videos and posters), in their order, then whatever videos the
    recipe picks for the time left, laid out as they will air (see
    :mod:`scheduling.layout`). A pinned video is never picked a second
    time."""
    from scheduling import layout

    videos = [entry for entry in pinned if isinstance(entry, Video)]
    for video in videos:
        context.record(video)
    chosen = {video.pk for video in videos}
    pool = [video for video in candidates(recipe, now) if video.pk not in chosen]
    selector = KINDS[recipe.kind].selector(pool, window, context, now)
    picks: list = list(pinned)
    cursor = layout.end_of(window.starttime, picks, padded=window.padded)
    while True:
        # Every pick is a programme, so where the next one would start
        # does not depend on which it is.
        previous = picks[-1] if picks else None
        start = layout.next_start(cursor, previous, _PROGRAMME, padded=window.padded)
        remaining = window.endtime - start
        if remaining <= timedelta(0):
            break
        pick = selector.pick(remaining)
        if pick is None:
            break
        picks.append(pick)
        cursor = start + pick.duration
    return picks


class _Programme:
    """Stands in for "some video" when asking where the next one starts."""


_PROGRAMME = _Programme()
