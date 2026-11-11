"""Fill rules: pools, orderings and the one packing loop.

A pool says where candidates come from, an ordering how the next one is
chosen. Both are small functions in a registry. Every ordering returns a
Selector -- "the next video no longer than the time left" -- and
:func:`plan` packs a grant back-to-back from its start with whatever the
selector hands it.

Filling is a pure function of the rule, the pool, the window, the grant
id and the shared ScheduleContext, so the nightly run can recompute
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
    FillRule.Pool.FILLER: lambda rule: FILLER,
}


def candidates(rule: FillRule) -> list[Video]:
    """The rule's pool, restricted to what may be listed.

    Every pool excludes zero-length videos. Only `latest` may pick a
    pending upload -- an entry made ahead of its file -- so a weekly
    regular can create next week's entry before the lock and upload
    later; the other orderings pick from finished files.
    """
    qs = Video.objects.listable().filter(POOLS[rule.pool](rule))
    if rule.ordering != FillRule.Ordering.LATEST:
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


def least_aired(pool, grant, context, now) -> Selector:
    """Fewest times on the air first: aired and compiled schedule items,
    plus every grant item the run has counted."""
    scheduled = dict(
        Video.objects.filter(pk__in=[v.pk for v in pool])
        .annotate(n=Count("scheduleitem"))
        .values_list("pk", "n")
    )

    def times(video: Video) -> int:
        uncompiled = context.times_played[video.pk] - context.compiled_plays[video.pk]
        return scheduled.get(video.pk, 0) + uncompiled

    # Older first among equals, so a tie is stable and fair.
    ordered = sorted(pool, key=lambda v: (times(v), v.created_time, v.pk))
    return InOrder(ordered, context)


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
    FillRule.Ordering.SHUFFLED: shuffled,
    FillRule.Ordering.WEIGHTED: weighted,
}


# -- The packing loop --------------------------------------------------------


def plan(
    rule: FillRule, grant: AirtimeGrant, context: ScheduleContext, now: datetime
) -> list[Video]:
    """What `rule` would list in `grant`, back-to-back from its start."""
    selector = ORDERINGS[rule.ordering](candidates(rule), grant, context, now)
    picks: list[Video] = []
    remaining = grant.duration
    while remaining > timedelta(0):
        video = selector.pick(remaining)
        if video is None:
            break
        picks.append(video)
        remaining -= video.duration
        if rule.mode == FillRule.Mode.ONE:
            break
    return picks
