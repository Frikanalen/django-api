"""Fill rules: pools, orderings and the one packing loop.

A pool says where candidates come from, an ordering how the next one is
chosen. Both are small functions in a registry. Every ordering returns a
Selector -- "the next video no longer than the time left" -- and
:func:`plan` packs a grant back-to-back from its start with whatever the
selector hands it.

Filling is a pure function of the rule, the pool, the window, the grant
id, what its owner pinned and the shared ScheduleContext, so the nightly run can recompute
every automatic grant and only write when the answer changed.
"""

import hashlib
import random
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from django.db.models import Count, Q

from fk.models import Video
from fk.models.video import FILLER
from scheduling.models import AirtimeGrant, FillRule
from scheduling.selection import ScheduleContext, Selector, WeightedSelector, default_rules

# -- Pools: where candidates come from --------------------------------------

POOLS: dict[str, Callable[[FillRule], Q]] = {
    FillRule.Pool.ORGANIZATION: lambda rule: Q(organization=rule.organization_id),
    FillRule.Pool.PLAYLIST: lambda rule: Q(playlists=rule.playlist_id),
    FillRule.Pool.SERIES: lambda rule: Q(series=rule.series_id),
    FillRule.Pool.FILLER: lambda rule: FILLER,
}


def candidates(rule: FillRule) -> list[Video]:
    """The rule's pool, restricted to what may be listed.

    Every pool excludes zero-length videos. Only `latest` and `premiere`
    may pick a pending upload -- an entry made ahead of its file -- so a
    weekly regular can create next week's entry and upload later; the
    other orderings pick from finished files.
    """
    qs = Video.objects.listable().filter(POOLS[rule.pool](rule))
    if rule.ordering not in (FillRule.Ordering.LATEST, FillRule.Ordering.PREMIERE):
        qs = qs.filter(proper_import=True)
    return list(qs.distinct().order_by("-created_time", "-id"))


# -- Orderings: how the next one is chosen ----------------------------------


class InOrder:
    """Hands out candidates in a fixed order, each at most once per grant.
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


def latest(pool, grant, context, now) -> Selector:
    return InOrder(pool, context)  # candidates() already sorts newest first


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


def least_aired(pool, grant, context, now) -> Selector:
    """Fewest times on the air first."""
    times = _times_aired(pool, context)
    # Older first among equals, so a tie is stable and fair.
    ordered = sorted(pool, key=lambda v: (times(v), v.created_time, v.pk))
    return InOrder(ordered, context)


def premiere(pool, grant, context, now) -> Selector:
    """What has never been on the air, newest first; then the archive,
    least aired first. A new upload leads the next window and then takes
    its place in the rotation, so a slot does not repeat itself weekly
    while it waits for the next one."""
    times = _times_aired(pool, context)
    unaired = [v for v in pool if times(v) == 0]  # the pool is newest first
    archive = sorted(
        (v for v in pool if times(v) > 0), key=lambda v: (times(v), v.created_time, v.pk)
    )
    return InOrder([*unaired, *archive], context)


def shuffled(pool, grant, context, now) -> Selector:
    """Ordered by a hash of grant and video: stable night to night, and a
    new upload slots in at one position without reshuffling the rest."""

    def key(video: Video) -> str:
        return hashlib.sha256(f"{grant.pk}:{video.pk}".encode()).hexdigest()

    return InOrder(sorted(pool, key=key), context)


def weighted(pool, grant, context, now) -> Selector:
    """The weighted draw from :mod:`scheduling.selection`, seeded by the
    grant id so it repeats as long as nothing real changed."""
    return WeightedSelector(pool, context, default_rules(now=now), rng=random.Random(grant.pk))


ORDERINGS: dict[str, Callable[..., Selector]] = {
    FillRule.Ordering.LATEST: latest,
    FillRule.Ordering.LEAST_AIRED: least_aired,
    FillRule.Ordering.PREMIERE: premiere,
    FillRule.Ordering.SHUFFLED: shuffled,
    FillRule.Ordering.WEIGHTED: weighted,
}


# -- The packing loop --------------------------------------------------------


def plan(
    rule: FillRule,
    grant: AirtimeGrant,
    context: ScheduleContext,
    now: datetime,
    pinned: Sequence[Video] = (),
) -> list[Video]:
    """What `grant` lists under `rule`: the `pinned` videos its owner
    chose, in their order, then whatever the rule picks for the time
    left, back-to-back from the grant's start. A pinned video is never
    picked a second time."""
    for video in pinned:
        context.record(video)
    chosen = {video.pk for video in pinned}
    pool = [video for video in candidates(rule) if video.pk not in chosen]
    selector = ORDERINGS[rule.ordering](pool, grant, context, now)
    picks: list[Video] = list(pinned)
    remaining = grant.duration - sum((v.duration for v in pinned), timedelta(0))
    while remaining > timedelta(0):
        if pinned and rule.mode == FillRule.Mode.ONE:
            break
        pick = selector.pick(remaining)
        if pick is None:
            break
        picks.append(pick)
        remaining -= pick.duration
        if rule.mode == FillRule.Mode.ONE:
            break
    return picks
