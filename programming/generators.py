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

from fk.models import Video
from fk.models.video import FILLER
from programming.models import Recipe
from programming.selection import ScheduleContext, Selector, WeightedSelector, default_rules


class Window(Protocol):
    pk: int

    @property
    def duration(self) -> timedelta: ...


# -- Candidates: which videos a recipe may use --------------------------------


def _uploads(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    return Video.objects.listable().filter(organization=recipe.organization_id)


def _series(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    if not hasattr(recipe, "series_source"):  # the series was deleted
        return _uploads(recipe, now)
    return Video.objects.listable().filter(series=recipe.series_source.series_id)


def _playlist(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    if not hasattr(recipe, "playlist_source"):
        return Video.objects.none()
    # Finished files only: a list is hand-picked from what exists.
    listed = recipe.playlist_source.videos.values("pk")
    return Video.objects.airable().filter(pk__in=listed)


def _jukebox(recipe: Recipe, now: datetime) -> QuerySet[Video]:
    videos = Video.objects.listable().filter(FILLER)
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

    def __init__(self, ordered: Sequence[Video], context: ScheduleContext) -> None:
        self._remaining = list(ordered)
        self._context = context

    def pick(self, remaining: timedelta) -> Video | None:
        for index, video in enumerate(self._remaining):
            if video.duration <= remaining:
                del self._remaining[index]
                self._context.record(video)
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
    return InOrder([*unaired, *archive], context)


def weighted(pool, window, context, now) -> Selector:
    """The weighted draw from :mod:`programming.selection`, seeded by the
    window so it repeats as long as nothing real changed."""
    return WeightedSelector(pool, context, default_rules(now=now), rng=random.Random(window.pk))


# -- The kinds ----------------------------------------------------------------


@dataclass(frozen=True)
class KindSpec:
    candidates: Callable[[Recipe, datetime], QuerySet[Video]]
    selector: Callable[..., Selector]


KINDS: dict[str, KindSpec] = {
    Recipe.Kind.UPLOADS: KindSpec(_uploads, premiere),
    Recipe.Kind.SERIES: KindSpec(_series, premiere),
    Recipe.Kind.PLAYLIST: KindSpec(_playlist, premiere),
    Recipe.Kind.JUKEBOX: KindSpec(_jukebox, weighted),
}


# -- The packing loop ---------------------------------------------------------


def plan(
    recipe: Recipe,
    window: Window,
    context: ScheduleContext,
    now: datetime,
    pinned: Sequence[Video] = (),
) -> list[Video]:
    """What `window` lists under `recipe`: the `pinned` videos its owner
    chose, in their order, then whatever the recipe picks for the time
    left, back-to-back from the start. A pinned video is never picked a
    second time."""
    for video in pinned:
        context.record(video)
    chosen = {video.pk for video in pinned}
    pool = [video for video in candidates(recipe, now) if video.pk not in chosen]
    selector = KINDS[recipe.kind].selector(pool, window, context, now)
    picks: list[Video] = list(pinned)
    remaining = window.duration - sum((v.duration for v in pinned), timedelta(0))
    while remaining > timedelta(0):
        pick = selector.pick(remaining)
        if pick is None:
            break
        picks.append(pick)
        remaining -= pick.duration
    return picks
