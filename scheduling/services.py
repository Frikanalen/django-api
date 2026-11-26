"""Every state change of grants, slots and the compiled schedule.

Models hold data and constraints; the API, the admin and the nightly job
all call these functions, so none of them can bypass the rules. Each
function runs in a transaction.

The nightly pipeline (:func:`run_nightly`) runs four steps with one
`now`, all safe to re-run:

1. issue  -- each recurring slot occurrence up to the end of the week
   after next becomes a grant, with the slot's recipe;
2. fill   -- every grant with a recipe is filled again around what
   its owner pinned, in chronological order with one shared
   ScheduleContext. Locked and compiled ones too, until a day before
   they air, so an upload made after the deadline still gets on;
3. lock   -- open, non-exempt grants past their deadline lock; items
   that have become unlistable are dropped;
4. compile -- locked grants (and exempt ones past their deadline) are
   written to Scheduleitem back-to-back from their start.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from fk.models import AsRun, Organization, Scheduleitem, Video
from programming.generators import plan
from programming.models import Recipe
from programming.selection import ScheduleContext
from programming.services import uploads_recipe
from scheduling import broadcast_time, layout
from scheduling.models import (
    GRAPHIC_SHORTEST,
    AirtimeGrant,
    GrantItem,
    Poster,
    RecurringSlot,
    Showing,
)

logger = logging.getLogger(__name__)

State = AirtimeGrant.State


def station_organization_id() -> int:
    return settings.STATION_ORGANIZATION_ID


def airs_live(video: Video) -> bool:
    """A live programme airs from its source until it has a file."""
    return hasattr(video, "live_transmission") and not video.proper_import


# -- Who may do what ---------------------------------------------------------


def may_act_for(user, organization: Organization) -> bool:
    """Whether `user` may program airtime on `organization`'s behalf.

    Staff always; otherwise a member or the editor of an approved
    Frikanalen member organization, with a confirmed identity. Nobody,
    staff included, programs for a suspended organization.
    """
    if not user.is_authenticated or organization.is_rogue:
        return False
    if user.is_staff:
        return True
    if not (user.identity_confirmed and organization.fkmember):
        return False
    return organization.editor_id == user.pk or organization.members.filter(pk=user.pk).exists()


def can_edit(grant: AirtimeGrant, user, now: datetime | None = None) -> bool:
    """Whether `user` may replace `grant`'s list right now.

    Members: while open and before the deadline, or until air on a
    lock-exempt grant. Staff: until the grant ends, whatever the state;
    saving a compiled grant recompiles it. Once it is on air, what has
    aired stays as it is; what is on air can be taken off (see
    :func:`_take_off_air`).
    """
    now = now or timezone.now()
    if grant.state == State.CANCELLED or not may_act_for(user, grant.organization):
        return False
    if user.is_staff:
        return grant.endtime > now
    if grant.lock_exempt:
        return grant.starttime > now
    return grant.state == State.OPEN and grant.locks_at > now


# -- Editing a grant ---------------------------------------------------------


# What a grant lists: videos, the owner's text posters, and (put there by
# staff) graphics for a length of time.
Entry = Video | Poster | Showing


# What starts this soon is treated as already on air: playout has cued it,
# and moving a programme in its last seconds can leave the screen black.
ON_AIR_MARGIN = timedelta(seconds=30)


def started_rows(grant: AirtimeGrant, now: datetime | None = None) -> list[Scheduleitem]:
    """The rows `grant` compiled that have aired, are on air or start
    within :data:`ON_AIR_MARGIN`, in order. Compiling keeps them as they
    are; only an explicit cut (:func:`_cut`) changes them."""
    now = now or timezone.now()
    if grant.starttime > now + ON_AIR_MARGIN:
        return []
    return list(
        grant.schedule_items.filter(starttime__lte=now + ON_AIR_MARGIN)
        .select_related("video", "poster", "graphic")
        .order_by("starttime")
    )


def _row_entry(row: Scheduleitem) -> Entry:
    if row.graphic_id is not None:
        return Showing(row.graphic, row.duration)
    entry = row.video if row.video_id is not None else row.poster
    assert entry is not None  # a grant compiles videos, posters and graphics only
    return entry


def _tail_origin(
    grant: AirtimeGrant, started: Sequence[Scheduleitem], now: datetime
) -> tuple[datetime, Entry | None]:
    """Where the list after `started` begins, and what it follows. Before
    air, that is the grant's start. On air, it is where the started rows
    end -- or, if that has passed (graphics are showing), the first time
    far enough ahead to change, on the grid for a padded grant. A grant
    that has ended is laid out as it was, from its start."""
    if grant.starttime > now + ON_AIR_MARGIN or grant.endtime <= now:
        return grant.starttime, None
    start = started[-1].endtime if started else grant.starttime
    previous = _row_entry(started[-1]) if started else None
    earliest = now + ON_AIR_MARGIN
    if start < earliest:
        start = layout.up_to_grid(earliest) if grant.padded else earliest
        previous = None
    return start, previous


def _cut(row: Scheduleitem, at: datetime) -> None:
    """Take `row` off the air at `at`: what has already played of it stays
    on the schedule, as history; if it hasn't begun, it goes."""
    if row.starttime >= at:
        row.delete()
    else:
        row.duration = at - row.starttime
        row.save(update_fields=["duration"])
    transaction.on_commit(cache.clear)


def _take_off_air(grant: AirtimeGrant, entries: Sequence[Entry], now: datetime) -> list[Entry]:
    """Apply a staff edit to a grant on air, returning the list to store.

    What has aired must stay at the head of `entries`. The programme on
    air (and anything about to start) stays on air while it follows them
    in `entries`; left out, it is taken off the air now. Cut short, it
    stays listed, so the list still tells what aired."""
    started = started_rows(grant, now)
    aired = [r for r in started if r.endtime <= now]
    if [_key(e) for e in entries[: len(aired)]] != [_key(_row_entry(r)) for r in aired]:
        raise ValidationError(
            {"items": "What has already aired must stay at the head of the list, as it is."}
        )
    stored = [_row_entry(r) for r in aired]
    rest = list(entries[len(aired) :])
    live = started[len(aired) :]
    while live and rest and _key(rest[0]) == _key(_row_entry(live[0])):
        stored.append(rest.pop(0))
        live.pop(0)
    for row in live:
        _cut(row, now)
        if row.starttime < now:
            stored.append(_row_entry(row))
    return stored + rest


def _check_graphics(grant: AirtimeGrant, entries: Sequence[Entry], user) -> None:
    """Only staff put graphics on the air. A list that keeps the graphics
    it has, unchanged, is anyone's to save."""
    if getattr(user, "is_staff", False):
        return
    had = [(i.graphic_id, i.duration) for i in grant.items.filter(graphic__isnull=False)]
    has = [(e.pk, e.duration) for e in entries if isinstance(e, Showing)]
    if has != had:
        raise PermissionDenied("Only staff can put graphics in a list.")


def validate_items(
    grant: AirtimeGrant, entries: Sequence[Entry], now: datetime | None = None
) -> None:
    """Local to the grant: it owns its window, so there are no airtime
    conflicts to check -- only that everything is listable and fits,
    laid out as it will air (see :mod:`scheduling.layout`)."""
    videos = [e for e in entries if isinstance(e, Video)]
    foreign = [
        e for e in entries if isinstance(e, Poster) and e.organization_id != grant.organization_id
    ]
    if foreign:
        raise ValidationError({"items": "A poster can only air in its own organization's airtime."})
    if any(isinstance(e, Showing) and e.duration < GRAPHIC_SHORTEST for e in entries):
        seconds = int(GRAPHIC_SHORTEST.total_seconds())
        raise ValidationError({"items": f"A graphic is on for at least {seconds} s."})
    listable = set(
        Video.objects.listable().filter(pk__in=[v.pk for v in videos]).values_list("pk", flat=True)
    )
    refused = [v for v in videos if v.pk not in listable]
    if refused:
        names = ", ".join(f"'{v.name}'" for v in refused)
        raise ValidationError(
            {
                "items": f"Not airable: {names}. A video needs an organization with a "
                "responsible editor and a duration (set it by hand for an upload "
                "that will follow)."
            }
        )
    started = started_rows(grant, now)
    start, previous = _tail_origin(grant, started, now or timezone.now())
    tail = entries[len(started) :]
    placed = layout.lay_out(start, tail, padded=grant.padded, previous=previous)
    end = placed[-1].end if placed else start
    if end > grant.endtime:
        total = end - grant.starttime
        raise ValidationError(
            {
                "items": f"The list runs {total} with the breaks between programmes, "
                f"which is longer than the window ({grant.duration})."
            }
        )


class _Keep:
    """Leave what the grant fills from as it is."""


KEEP = _Keep()


def set_items(
    grant: AirtimeGrant,
    videos: Sequence[Entry],
    *,
    user,
    now: datetime | None = None,
    automatic: bool = False,
    recipe: Recipe | _Keep = KEEP,
    whole_slot: bool = False,
) -> AirtimeGrant:
    """Save the list the organization (or staff) chose: videos and
    posters, in order. Every entry given is pinned.

    With `automatic`, a recipe fills the time the pinned videos leave,
    now and on every later run: `recipe` if given, otherwise the one the
    grant has (see :func:`recipe_for`). `whole_slot` makes `recipe` the
    slot's too, for the other broadcasts it has issued and the ones to
    come.

    Without `automatic` the grant is hand-made: the recipe is cleared
    and the list stays exactly as saved. A grant that is already
    compiled is recompiled at once.
    """
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        if not can_edit(grant, user, now):
            raise PermissionDenied("This grant can no longer be edited.")
        _check_graphics(grant, videos, user)
        videos = _take_off_air(grant, videos, now)
        validate_items(grant, videos, now)
        if isinstance(recipe, Recipe) and recipe.organization_id != grant.organization_id:
            raise ValidationError({"recipe": "The recipe must belong to the grant's organization."})
        _replace_items(grant, videos, pinned=len(videos))
        if not automatic:
            grant.recipe = None
        elif isinstance(recipe, Recipe):
            grant.recipe = recipe
        else:
            grant.recipe = recipe_for(grant)
        grant.save(update_fields=["recipe", "updated_at"])
        if automatic:
            _fill(grant, _context_for([grant], now), now)
        if _on_schedule(grant, now):
            compile_grant(grant, now)
        if automatic and whole_slot and isinstance(recipe, Recipe):
            if grant.origin_slot is None:
                raise ValidationError({"whole_slot": "This broadcast has no recurring slot."})
            set_slot_recipe(grant.origin_slot, recipe, user=user, now=now)
    return grant


def replace_video(
    grant: AirtimeGrant, old: Video | None, new: Video, *, now: datetime | None = None
) -> bool:
    """Put `new` in `old`'s place in the grant's list (with `old` None:
    first, ahead of the chosen entries), and on the schedule if the grant
    is there, before the grant goes on air. Returns whether it did: not
    once the grant has begun, nor if `new` doesn't fit."""
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        if grant.starttime <= now or grant.state not in (State.OPEN, State.LOCKED, State.COMPILED):
            return False
        items = list(grant.items.select_related("video", "poster", "graphic"))
        # The chosen (pinned) entries, `new` in `old`'s place; what the
        # recipe filled around them is filled afresh below.
        if old is None:
            if any(item.video_id == new.pk for item in items if item.pinned):
                return False
            entries = [new] + [_row_entry(item) for item in items if item.pinned]
        else:
            if not any(item.video_id == old.pk for item in items):
                return False
            kept = [item for item in items if item.pinned or item.video_id == old.pk]
            entries = [new if item.video_id == old.pk else _row_entry(item) for item in kept]
        pinned = len(entries)
        try:
            validate_items(grant, entries, now)
        except ValidationError:
            return False
        _replace_items(grant, entries, pinned=pinned)
        grant.save(update_fields=["updated_at"])
        if grant.recipe is not None:  # what the new video leaves, filled afresh
            _fill(grant, _context_for([grant], now), now)
        if _on_schedule(grant, now):
            compile_grant(grant, now)
    return True


def refill(grant: AirtimeGrant, *, now: datetime | None = None) -> bool:
    """Fill what the grant's list leaves of its window from its recipe
    again, before it goes on air (say, once a video's real duration is
    known). Returns whether the list changed."""
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        if grant.recipe is None or grant.starttime <= now or grant.state == State.CANCELLED:
            return False
        if not _fill(grant, _context_for([grant], now), now):
            return False
        grant.save(update_fields=["updated_at"])
        if _on_schedule(grant, now):
            compile_grant(grant, now)
    return True


def make_automatic(grant: AirtimeGrant, *, user, now: datetime | None = None) -> AirtimeGrant:
    """Unpin everything and let the grant's recipe program the whole window."""
    return set_items(grant, [], user=user, now=now, automatic=True)


def recipe_for(grant: AirtimeGrant) -> Recipe:
    """The recipe an automatic grant fills by: the one it has, else its
    slot's, else everything the owner uploads."""
    if grant.recipe is not None:
        return grant.recipe
    slot_recipe = grant.origin_slot.recipe if grant.origin_slot else None
    return slot_recipe or uploads_recipe(grant.organization)


def create_grant(
    *,
    organization: Organization,
    starttime: datetime,
    endtime: datetime,
    locks_at: datetime | None = None,
    lock_exempt: bool = False,
    recipe: Recipe | None = None,
    videos: Sequence[Entry] = (),
    now: datetime | None = None,
) -> AirtimeGrant:
    """Staff gives an organization a window directly, optionally with the
    videos it plays already in it (pinned, in order). Everything happens
    together: if the videos don't fit, no grant is made.

    `now` is the caller's idea of the time, for a caller that has one --
    the Stortinget keeper decides on a clock of its own. Whether the list
    fits depends on it: a grant already on air is laid out from now, not
    from its start, so checking against the wall clock instead would
    judge the keeper's booking by a different moment than it was made in."""
    if organization.is_rogue:
        raise ValidationError({"organization": "This organization is suspended."})
    grant = AirtimeGrant(
        organization=organization,
        starttime=starttime,
        endtime=endtime,
        locks_at=locks_at or broadcast_time.default_locks_at(starttime),
        lock_exempt=lock_exempt,
        recipe=recipe,
    )
    grant.full_clean()
    with transaction.atomic():
        grant.save()
        if videos:
            validate_items(grant, videos, now)
            _replace_items(grant, videos, pinned=len(videos))
        _publish_if_due(grant, now)
    return grant


def _on_schedule(grant: AirtimeGrant, now: datetime) -> bool:
    """Whether the grant's list belongs on the published schedule now:
    it is compiled, or it is lock-exempt and past its deadline, which the
    nightly run would compile anyway. Waiting for that run would leave a
    grant given (or set up) today off the schedule until after midnight,
    perhaps after it aired."""
    if grant.state == State.COMPILED:
        return True
    return grant.state == State.OPEN and grant.lock_exempt and grant.locks_at <= now


def _publish_if_due(grant: AirtimeGrant, now: datetime | None = None) -> None:
    """A new grant that is already due fills from its recipe and goes on
    the schedule at once, as the nightly run would have done."""
    now = now or timezone.now()
    if not _on_schedule(grant, now):
        return
    if grant.recipe is not None:
        _fill(grant, _context_for([grant], now), now)
    compile_grant(grant, now)


def approve_airtime(airtime_request) -> AirtimeGrant:
    """The grant an approved airtime request becomes. It fills itself from
    the organization's newest uploads until they set up a list. Approved
    after the usual deadline, it is lock-exempt, so they still can."""
    now = timezone.now()
    organization = airtime_request.organization
    if organization.is_rogue:
        raise ValidationError({"organization": "This organization is suspended."})
    if airtime_request.starttime <= now:
        raise ValidationError({"starttime": "The requested time has already started."})
    taken = AirtimeGrant.objects.active().filter(
        starttime__lt=airtime_request.endtime, endtime__gt=airtime_request.starttime
    )
    if taken.exists():
        holder = taken.select_related("organization").first()
        raise ValidationError(
            {
                "starttime": f"{holder.organization} already has airtime then ({holder.starttime:%Y-%m-%d %H:%M})."
            }
        )
    locks_at = broadcast_time.default_locks_at(airtime_request.starttime)
    try:
        return create_grant(
            organization=organization,
            starttime=airtime_request.starttime,
            endtime=airtime_request.endtime,
            locks_at=locks_at,
            lock_exempt=locks_at <= now,
            recipe=uploads_recipe(organization),
        )
    except IntegrityError as error:  # lost a race for the window
        raise ValidationError({"starttime": "Someone else got this airtime first."}) from error


def update_grant(grant: AirtimeGrant, *, now: datetime | None = None, **changes) -> AirtimeGrant:
    """Staff changes a grant's deadline, lock exemption, start or end.

    A new end may shorten the grant, even on air: what is on air past it
    is cut short there, and the entries that no longer fit come off the
    list and the schedule. Lengthening it needs the extra time to be
    free. The start can move until the grant goes on air; a start that
    has passed means now."""
    allowed = {"locks_at", "lock_exempt", "starttime", "endtime"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationError({field: "Cannot be changed." for field in unknown})
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        starttime = changes.pop("starttime", None)
        endtime = changes.pop("endtime", None)
        for field, value in changes.items():
            setattr(grant, field, value)
        grant.save(update_fields=[*changes, "updated_at"])
        if starttime is not None and starttime != grant.starttime:
            _move_start(grant, max(starttime, now), endtime or grant.endtime, now)
        if endtime is not None and endtime != grant.endtime:
            _move_end(grant, endtime, now)
    return grant


def _move_start(grant: AirtimeGrant, starttime: datetime, endtime: datetime, now: datetime) -> None:
    if grant.state == State.CANCELLED:
        raise ValidationError({"starttime": "This grant is cancelled."})
    if grant.starttime <= now:
        raise ValidationError({"starttime": "This grant is on air; its start can't move."})
    if starttime >= endtime:
        raise ValidationError({"starttime": "The grant must start before it ends."})
    if starttime < grant.starttime:
        blocking = (
            Scheduleitem.objects.overlapping(starttime, grant.starttime)
            .exclude(grant=grant)
            .first()
        )
        if blocking is not None:
            raise ValidationError({"starttime": f"The extra time is taken by {blocking}."})
    if endtime > grant.endtime:
        blocking = (
            Scheduleitem.objects.overlapping(grant.endtime, endtime).exclude(grant=grant).first()
        )
        if blocking is not None:
            raise ValidationError({"endtime": f"The extra time is taken by {blocking}."})
    # Start and end move in one write: the database checks the grant's rows
    # against each window it is given, so a halfway window (the new start
    # with the old end) would refuse rows that fit the final one.
    grant.starttime, grant.endtime = starttime, endtime
    try:
        with transaction.atomic():
            grant.save(update_fields=["starttime", "endtime", "updated_at"])
    except IntegrityError as error:
        raise ValidationError({"starttime": "The new time overlaps another grant."}) from error
    _refit(grant, now)


def _refit(grant: AirtimeGrant, now: datetime) -> None:
    """After the window moved: drop what no longer fits (the head that has
    aired stays), refill a self-filling grant that hasn't started, and
    put the result on the schedule."""
    started = started_rows(grant, now)
    items = list(grant.items.select_related("video", "poster", "graphic"))
    head, tail = items[: len(started)], items[len(started) :]
    start, previous = _tail_origin(grant, started, now)
    kept = head + _fitting(grant, tail, padded=grant.padded, start=start, previous=previous)
    if len(kept) < len(items):
        _replace_items(grant, [i.entry for i in kept], pinned=sum(i.pinned for i in kept))
    if grant.recipe is not None and not started:
        _fill(grant, _context_for([grant], now), now)
    if _on_schedule(grant, now):
        compile_grant(grant, now)


def _move_end(grant: AirtimeGrant, endtime: datetime, now: datetime) -> None:
    if grant.state == State.CANCELLED:
        raise ValidationError({"endtime": "This grant is cancelled."})
    if grant.endtime <= now:
        raise ValidationError({"endtime": "This grant has already aired."})
    if endtime <= grant.starttime:
        raise ValidationError({"endtime": "The grant must end after it starts."})
    if endtime <= now:
        raise ValidationError({"endtime": "The grant can't end in the past."})
    # Ending before what is on air finishes cuts it short at the new end.
    for row in started_rows(grant, now):
        if row.endtime > endtime:
            _cut(row, endtime)
    if endtime > grant.endtime:
        blocking = (
            Scheduleitem.objects.overlapping(grant.endtime, endtime).exclude(grant=grant).first()
        )
        if blocking is not None:
            raise ValidationError({"endtime": f"The extra time is taken by {blocking}."})
    grant.endtime = endtime
    try:
        with transaction.atomic():
            grant.save(update_fields=["endtime", "updated_at"])
    except IntegrityError as error:
        raise ValidationError({"endtime": "The extra time overlaps another grant."}) from error
    _refit(grant, now)


def cancel_grant(grant: AirtimeGrant, *, now: datetime | None = None) -> AirtimeGrant:
    """Withdraw a grant, freeing its window. Whatever it compiled that has
    not aired yet comes off the schedule; what is on air is cut short now."""
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        for row in grant.schedule_items.filter(starttime__lt=now):
            if row.endtime > now:
                _cut(row, now)
        grant.schedule_items.filter(starttime__gte=now).delete()
        grant.state = State.CANCELLED
        grant.save(update_fields=["state", "updated_at"])
    return grant


def set_slot_recipe(
    slot: RecurringSlot, recipe: Recipe, *, user, now: datetime | None = None
) -> RecurringSlot:
    """The owner (or staff) chooses how a slot programs itself.

    The grants the slot already issued follow, as long as they still fill
    themselves and are not past the point where automatic grants stop
    changing; hand-made lists are left alone.
    """
    now = now or timezone.now()
    owner = slot.organization
    if owner is None or not may_act_for(user, owner):
        raise PermissionDenied("You can't program this slot.")
    if recipe.organization_id != owner.pk:
        raise ValidationError({"recipe": "The recipe must belong to the slot's organization."})
    with transaction.atomic():
        slot.recipe = recipe
        slot.save(update_fields=["recipe"])
        following = list(
            slot.grants.filter(recipe__isnull=False)
            .exclude(recipe=recipe)
            .filter(
                Q(state=State.OPEN)
                | Q(state__in=(State.LOCKED, State.COMPILED), starttime__gt=now + REFRESH_UNTIL)
            )
            .filter(starttime__gt=now)
            .order_by("starttime")
        )
        context = _context_for(following, now)
        for grant in following:
            grant.recipe = recipe
            grant.save(update_fields=["recipe", "updated_at"])
            if _fill(grant, context, now) and grant.state == State.COMPILED:
                compile_grant(grant, now)
    return slot


# How far ahead slot_conflicts looks. Long enough to catch slots that
# meet only every few weeks or months; short enough to stay cheap.
CONFLICT_HORIZON = timedelta(weeks=26)


def slot_conflicts(
    slot: RecurringSlot, now: datetime | None = None
) -> list[tuple[RecurringSlot, datetime]]:
    """Other slots whose occurrences overlap `slot`'s in the coming
    half-year, with the first instant `slot` would collide with each.

    Slots of suspended organizations don't count: they issue no grants.
    A collision means issue_grants would skip one of the two occurrences,
    so the editor refuses it rather than let a slot silently go empty.
    """
    now = now or timezone.now()
    start = max(now, broadcast_time.local_midnight(slot.starts_on))
    end = start + CONFLICT_HORIZON
    mine = [(t, t + slot.duration) for t in slot.occurrences(start, end)]
    if not mine:
        return []
    others = RecurringSlot.objects.exclude(organization__is_rogue=True).exclude(
        ends_on__lt=broadcast_time.local_date(start)
    )
    if slot.pk:
        others = others.exclude(pk=slot.pk)
    conflicts = []
    for other in others.select_related("organization"):
        # Start a day early so an occurrence that began before `start`
        # but is still running counts too.
        theirs = [
            (t, t + other.duration) for t in other.occurrences(start - timedelta(days=1), end)
        ]
        hit = _first_overlap(mine, theirs)
        if hit is not None:
            conflicts.append((other, hit))
    return conflicts


def _first_overlap(
    mine: list[tuple[datetime, datetime]], theirs: list[tuple[datetime, datetime]]
) -> datetime | None:
    """The start of the first window in `mine` that overlaps one in
    `theirs`. Both lists are in order and hold no overlaps of their own."""
    i = j = 0
    while i < len(mine) and j < len(theirs):
        (a, b), (c, d) = mine[i], theirs[j]
        if a < d and c < b:
            return a
        if b <= d:
            i += 1
        else:
            j += 1
    return None


def create_weekly_slot(
    *, organization: Organization, day: int, start_time: time, duration: timedelta
) -> RecurringSlot:
    """A weekly slot from an approved creation request, filling itself from
    everything the owner uploads."""
    return RecurringSlot.objects.create(
        organization=organization,
        frequency=RecurringSlot.Frequency.WEEKLY,
        weekdays=[day],
        start_time=start_time,
        duration=duration,
        starts_on=broadcast_time.local_date(timezone.now()),
        recipe=uploads_recipe(organization),
    )


def transfer_slot(slot: RecurringSlot, organization: Organization) -> None:
    """Give a slot to a new owner. The old owner's recipe stays with them;
    the new owner gets a default one. Issued grants keep their owner."""
    slot.organization = organization
    slot.recipe = uploads_recipe(organization)
    slot.save(update_fields=["organization", "recipe"])


# -- Text posters ------------------------------------------------------------


def create_poster(*, organization: Organization, user, **fields) -> Poster:
    """A member (or staff) makes a text poster for their organization."""
    if not may_act_for(user, organization):
        raise PermissionDenied("You can't make posters for this organization.")
    poster = Poster(organization=organization, **fields)
    if "duration" not in fields:
        poster.duration = max(poster.duration, poster.minimum_duration)
    poster.full_clean()
    poster.save()
    return poster


def _coming_grants(poster: Poster, now: datetime):
    """The grants yet to end that list `poster`."""
    return (
        AirtimeGrant.objects.active()
        .filter(items__poster=poster, endtime__gt=now)
        .distinct()
        .order_by("starttime")
    )


def update_poster(poster: Poster, *, user, now: datetime | None = None, **changes) -> Poster:
    """Change a poster's text, template or length.

    The text can be corrected until it airs, in every grant that lists
    it, as long as it can still be read in the poster's length. Its length changes the schedule, so it can only change where the
    user could edit the list too, and only if the list still fits; the
    compiled grants that list it are recompiled at once, which also tells
    playout to show the new text.
    """
    now = now or timezone.now()
    if not may_act_for(user, poster.organization):
        raise PermissionDenied("You can't change this poster.")
    with transaction.atomic():
        poster = Poster.objects.select_for_update().get(pk=poster.pk)
        old_duration = poster.duration
        for field, value in changes.items():
            setattr(poster, field, value)
        poster.full_clean()
        grants = list(_coming_grants(poster, now).filter(starttime__gt=now))
        if poster.duration != old_duration:
            for grant in grants:
                if not can_edit(grant, user, now):
                    raise ValidationError(
                        {
                            "duration": "The poster is in a list that is locked "
                            f"({_when(grant)}), so its length can't change."
                        }
                    )
                entries = [
                    poster if item.poster_id == poster.pk else item.entry
                    for item in grant.items.select_related("video", "poster", "graphic")
                ]
                validate_items(grant, entries)
        poster.save()
        for grant in grants:
            if _on_schedule(grant, now):
                compile_grant(grant, now)
    return poster


def delete_poster(poster: Poster, *, user, now: datetime | None = None) -> None:
    """Delete a poster nothing coming lists. Lists that have aired forget it."""
    now = now or timezone.now()
    if not may_act_for(user, poster.organization):
        raise PermissionDenied("You can't delete this poster.")
    with transaction.atomic():
        coming = _coming_grants(poster, now).first()
        if coming is not None:
            raise ValidationError(
                f"The poster is in the airtime {_when(coming)}. Take it out of that list first."
            )
        GrantItem.objects.filter(poster=poster).delete()
        poster.delete()


def delete_video(video: Video, *, now: datetime | None = None) -> None:
    """Delete a video, and take it out of every list and off the schedule.

    Coming grants forget it: the list closes up, an automatic grant's
    recipe fills the time it leaves, and a compiled grant is recompiled.
    A grant on air loses it from what is still to come, its started rows
    left as they are. Staff entries yet to air go.

    What has aired keeps its place in the history: past schedule rows and
    the as-run log keep the title, without the video. Lists that have aired
    forget it, as they do a deleted poster.

    Refused while the video is on air or about to start (within
    :data:`ON_AIR_MARGIN`), and while a grant that has already aired it is
    still on air: the started part of that grant must stay as it aired.
    Both are over within the hour or two the grant lasts.
    """
    now = now or timezone.now()
    with transaction.atomic():
        video = Video.objects.select_for_update().get(pk=video.pk)
        rows = Scheduleitem.objects.filter(video=video)
        on_air = rows.overlapping(now, now + ON_AIR_MARGIN).order_by("starttime").first()
        if on_air is not None:
            raise ValidationError(
                f"Videoen er på lufta nå (til {_clock(on_air.endtime)}). "
                "Du kan slette den når den er ferdig."
            )
        running = (
            AirtimeGrant.objects.filter(
                schedule_items__video=video,
                schedule_items__starttime__lte=now,
                endtime__gt=now,
            )
            .order_by("endtime")
            .first()
        )
        if running is not None:
            raise ValidationError(
                "Videoen har gått i en sending som fortsatt pågår "
                f"(til {_clock(running.endtime)}). Du kan slette den når sendingen er ferdig."
            )

        listing = (
            GrantItem.objects.filter(video=video, grant__endtime__gt=now)
            .exclude(grant__state=State.CANCELLED)
            .values("grant_id")
        )
        grants = list(
            AirtimeGrant.objects.select_for_update(of=("self",))
            .filter(pk__in=listing)
            .select_related("organization", "recipe")
            .order_by("starttime")
        )
        for grant in grants:
            items = list(grant.items.select_related("video", "poster", "graphic"))
            kept = [item for item in items if item.video_id != video.pk]
            pinned = sum(1 for item in kept if item.pinned)
            _replace_items(grant, [item.entry for item in kept], pinned=pinned)
            grant.save(update_fields=["updated_at"])
        # Lists that have aired, and cancelled ones, simply forget it.
        GrantItem.objects.filter(video=video).delete()

        # What is still to come goes; compile_grant rewrites a grant's own
        # rows below, and a staff entry has nothing to stand in for it.
        rows.filter(starttime__gt=now).delete()
        # What aired keeps its title once the video is gone.
        rows.filter(default_name="").update(default_name=video.name[:255])
        AsRun.objects.filter(video=video, program_name="").update(program_name=video.name[:160])
        video.delete()

        # The video is gone, so a recipe can't pick it again.
        for grant in grants:
            if grant.recipe is not None and grant.starttime > now + ON_AIR_MARGIN:
                _fill(grant, _context_for([grant], now), now)
            if _on_schedule(grant, now):
                compile_grant(grant, now)
        transaction.on_commit(cache.clear)


def _clock(at: datetime) -> str:
    return f"{at.astimezone(broadcast_time.TZ):%H:%M}"


def _when(grant: AirtimeGrant) -> str:
    return f"{grant.starttime.astimezone(broadcast_time.TZ):%Y-%m-%d %H:%M}"


# -- The nightly pipeline ----------------------------------------------------


def run_nightly(now: datetime | None = None) -> None:
    now = now or timezone.now()
    issue_grants(now)
    fill_grants(now)
    lock_grants(now)
    compile_grants(now)


def issue_grants(now: datetime) -> list[AirtimeGrant]:
    """Turn every slot occurrence from `now` to the end of the week after
    next into a grant, unless one was already issued for it (cancelled
    ones count). A window someone else already holds is skipped."""
    horizon = broadcast_time.issue_horizon(now)
    station = station_organization_id()
    station_exists = Organization.objects.filter(pk=station).exists()
    issued = []
    # A suspended (is_rogue) member keeps its slots but is issued nothing.
    slots = RecurringSlot.objects.select_related("recipe").exclude(organization__is_rogue=True)
    for slot in slots:
        if slot.organization_id is None and not station_exists:
            logger.error(
                "Not issuing unowned %s: STATION_ORGANIZATION_ID %s is missing", slot, station
            )
            continue
        taken = set(
            slot.grants.filter(starttime__gte=now, starttime__lt=horizon).values_list(
                "starttime", flat=True
            )
        )
        for starttime in slot.occurrences(now, horizon):
            if starttime in taken:
                continue
            grant = AirtimeGrant(
                organization_id=slot.organization_id or station,
                starttime=starttime,
                endtime=starttime + slot.duration,  # an instant: elapsed time
                locks_at=broadcast_time.default_locks_at(starttime),
                recipe=slot.recipe,
                origin_slot=slot,
            )
            try:
                with transaction.atomic():
                    grant.save()
            except IntegrityError:
                logger.warning(
                    "Not issuing %s at %s: the airtime is granted to someone else",
                    slot,
                    starttime,
                )
                continue
            issued.append(grant)
    return issued


# How long before air an automatic grant that is already locked or
# compiled stops picking up changes: the published schedule is firm for
# the coming day.
REFRESH_UNTIL = timedelta(hours=24)


def fill_grants(now: datetime) -> None:
    """Refill every grant that has a recipe, oldest first, with one
    shared context: open ones, and locked or compiled ones still more
    than REFRESH_UNTIL from air. Items change only when the answer did;
    a compiled grant whose answer changed is recompiled."""
    grants = list(
        AirtimeGrant.objects.filter(recipe__isnull=False)
        .filter(
            Q(state=State.OPEN)
            | Q(state__in=(State.LOCKED, State.COMPILED), starttime__gt=now + REFRESH_UNTIL)
        )
        .select_related("recipe")
        .order_by("starttime")
    )
    context = _context_for(grants, now)
    for grant in grants:
        with transaction.atomic():
            if _fill(grant, context, now) and grant.state == State.COMPILED:
                compile_grant(grant, now)


def lock_grants(now: datetime) -> None:
    """Lock open, non-exempt grants whose deadline has passed. Items that
    can no longer be listed are dropped; pending uploads stay."""
    due = AirtimeGrant.objects.filter(state=State.OPEN, lock_exempt=False, locks_at__lte=now)
    for grant in due:
        with transaction.atomic():
            items = list(grant.items.select_related("video", "poster", "graphic"))
            listable = set(
                Video.objects.listable()
                .filter(pk__in=[i.video_id for i in items if i.video_id])
                .values_list("pk", flat=True)
            )
            kept = [i for i in items if i.poster_id or i.graphic_id or i.video_id in listable]
            if len(kept) != len(items):
                dropped = [i.video_id for i in items if i not in kept]
                logger.warning("Dropping unlistable videos %s from %s at lock", dropped, grant)
                _replace_items(grant, [i.entry for i in kept], pinned=sum(i.pinned for i in kept))
            grant.state = State.LOCKED
            grant.save(update_fields=["state", "updated_at"])


def compile_grants(now: datetime) -> None:
    due = AirtimeGrant.objects.filter(
        Q(state=State.LOCKED) | Q(state=State.OPEN, lock_exempt=True, locks_at__lte=now)
    ).order_by("starttime")
    for grant in due:
        compile_grant(grant, now)


def compile_grant(grant: AirtimeGrant, now: datetime | None = None) -> bool:
    """Write the grant's items to Scheduleitem as :mod:`scheduling.layout`
    places them, replacing whatever it compiled before. Returns whether
    it did.

    On air, the rows that have started (see :func:`started_rows`) stay
    as they are, untouched, and stand for the head of the list; only
    what comes after them is rewritten, laid out from where they end.

    A grant owns its window, so normally nothing else is there. If
    something is -- a staff entry, or a row from before the cut-over --
    the grant is left as it was and the conflict logged.

    A suspended (is_rogue) member's grant compiles to nothing: what it
    put on the schedule and has not aired yet comes off, and the grant
    is left in its state.

    A removed video (Video.is_banned) is skipped, the list closing up
    behind it, as is one the recipe picked that is no longer published.
    """
    now = now or timezone.now()
    if grant.organization.is_rogue:
        grant.schedule_items.filter(starttime__gt=now).delete()
        logger.warning("Not compiling %s: %s is suspended", grant, grant.organization)
        return False
    try:
        with transaction.atomic():
            started = started_rows(grant, now)
            grant.schedule_items.exclude(pk__in=[r.pk for r in started]).delete()
            blocking = (
                Scheduleitem.objects.overlapping(grant.starttime, grant.endtime)
                .exclude(grant=grant)
                .first()
            )
            if blocking is not None:
                raise _Blocked(blocking)
            items = list(
                grant.items.select_related("video__live_transmission", "poster", "graphic")
            )
            heads = [_key(_row_entry(r)) for r in started]
            if [_key(i.entry) for i in items[: len(started)]] != heads:
                logger.warning("%s: the list no longer starts with what has aired", grant)
            items = items[len(started) :]
            # A video staff have removed (is_banned) stays on the list, so
            # lifting the removal puts it back, but is not laid out: the
            # rest of the list closes up behind it.
            removed = [i for i in items if i.video is not None and i.video.is_banned]
            for item in removed:
                logger.warning("Leaving %s out of %s: it has been removed", item.entry, grant)
            items = [i for i in items if i not in removed]
            # Nor is a video the recipe picked that has since been
            # unpublished: only what someone listed by hand airs unpublished.
            withdrawn = [i for i in items if _withdrawn_pick(grant, i)]
            for item in withdrawn:
                logger.warning("Leaving %s out of %s: it is no longer published", item.entry, grant)
            items = [i for i in items if i not in withdrawn]
            start, previous = _tail_origin(grant, started, now)
            padded = grant.padded
            kept = _fitting(grant, items, padded=padded, start=start, previous=previous)
            if len(kept) < len(items) and padded:
                # A list saved before programmes were padded may only fit
                # back to back. Better that than leaving a programme out.
                unpadded = _fitting(grant, items, padded=False, start=start, previous=previous)
                if len(unpadded) > len(kept):
                    logger.warning("%s only fits back to back; not padding it", grant)
                    kept, padded = unpadded, False
            for item in items:
                if item not in kept:
                    logger.warning(
                        "Leaving %s out of %s: it no longer fits the window", item.entry, grant
                    )
            placed = layout.lay_out(
                start, [i.entry for i in kept], padded=padded, previous=previous
            )
            for item, where in zip(kept, placed, strict=True):
                Scheduleitem.objects.create(
                    video=item.video,
                    poster=item.poster,
                    graphic=item.graphic,
                    starttime=where.start,
                    duration=where.end - where.start,
                    grant=grant,
                    is_live=item.video is not None and airs_live(item.video),
                )
            grant.state = State.COMPILED
            grant.save(update_fields=["state", "updated_at"])
    except _Blocked as blocked:
        logger.error("Cannot compile %s: the window holds %s", grant, blocked.item)
        return False
    return True


def _withdrawn_pick(grant: AirtimeGrant, item: GrantItem) -> bool:
    """Whether `item` is a video `grant`'s recipe picked (not one its owner
    pinned) that is no longer published, so the recipe could not pick it
    now. Such a video must not air on the recipe's say-so."""
    return (
        grant.recipe_id is not None
        and not item.pinned
        and item.video is not None
        and not item.video.publish_on_web
    )


def _withdraw_unpublished(video: Video, now: datetime) -> None:
    """`video` is unpublished: take it out of what recipes picked it for.

    A grant yet to start is filled from its recipe again, so the time it
    leaves is programmed, and recompiled if it is on the schedule. One on
    air, or about to start, is recompiled: what has started stays, the
    rest closes up. This holds inside the day the schedule is otherwise
    firm for: a video its owner has withdrawn should not air regardless.
    """
    grants = (
        AirtimeGrant.objects.filter(
            recipe__isnull=False,
            endtime__gt=now,
            items__video=video,
            items__pinned=False,
        )
        .exclude(state=State.CANCELLED)
        .distinct()
        .order_by("starttime")
    )
    for grant in grants:
        with transaction.atomic():
            grant = (
                AirtimeGrant.objects.select_for_update(of=("self",))
                .select_related("organization", "recipe")
                .get(pk=grant.pk)
            )
            coming = grant.starttime > now + ON_AIR_MARGIN
            if coming and _fill(grant, _context_for([grant], now), now):
                grant.save(update_fields=["updated_at"])
                logger.warning("Refilled %s: %s is no longer published", grant, video)
            if _on_schedule(grant, now):
                compile_grant(grant, now)
        transaction.on_commit(cache.clear)


def recompile_for_video(video: Video, now: datetime | None = None) -> None:
    """A pending upload's file arrived with its real length, a live
    programme got its recording, or staff removed the video or lifted
    its removal: recompile the compiled grants yet to air whose rows no
    longer match it. A save that changes nothing about the length, how
    it airs or whether it may air leaves the schedule alone.

    An unpublished video also leaves every list a recipe picked it for
    (see :func:`_withdraw_unpublished`)."""
    now = now or timezone.now()
    # Fresh, so a live transmission just added or removed is seen.
    video = Video.objects.select_related("live_transmission").get(pk=video.pk)
    if not video.publish_on_web:
        _withdraw_unpublished(video, now)
    live = airs_live(video)
    # A removed video comes off a grant already on air too: compile_grant
    # keeps what has started and closes up the rest.
    window = Q(endtime__gt=now) if video.is_banned else Q(starttime__gt=now)
    grants = AirtimeGrant.objects.filter(
        window, state=State.COMPILED, items__video=video
    ).distinct()
    # Staff entries have no grant to recompile; they only follow how it airs.
    # .update() sends no signals, so the page cache is cleared by hand.
    if (
        Scheduleitem.objects.filter(video=video, grant=None, starttime__gt=now)
        .exclude(is_live=live)
        .update(is_live=live)
    ):
        transaction.on_commit(cache.clear)
    for grant in grants:
        rows = list(grant.schedule_items.filter(video=video).values_list("duration", "is_live"))
        # A removed video is listed but not laid out; see compile_grant.
        items = grant.items.filter(video=video).select_related("video")
        listed = (
            0 if video.is_banned else sum(1 for item in items if not _withdrawn_pick(grant, item))
        )
        if len(rows) != listed or any(d != video.duration or l != live for d, l in rows):
            compile_grant(grant, now)


def cut_over(at: datetime) -> int:
    """Start the new schedule at `at`.

    Wipes every pre-cut-over row (anything with a schedule reason)
    starting at or after `at`, then runs the pipeline as of `at`: every
    slot occurrence gets a grant, and those past their default deadline
    -- the current and next week -- are filled and compiled at once.
    Safe to re-run: rows written under the new rules are kept.
    """
    with transaction.atomic():
        deleted, _ = Scheduleitem.objects.filter(
            starttime__gte=at, schedulereason__isnull=False
        ).delete()
    run_nightly(now=at)
    return deleted


# -- Internals ---------------------------------------------------------------


class _Blocked(Exception):
    def __init__(self, item: Scheduleitem) -> None:
        self.item = item


def _replace_items(grant: AirtimeGrant, entries: Sequence[Entry], pinned: int = 0) -> None:
    """Pinned items lead the list, so the first `pinned` entries are the
    pinned ones."""
    grant.items.all().delete()
    GrantItem.objects.bulk_create(
        GrantItem(
            grant=grant,
            position=position,
            video=entry if isinstance(entry, Video) else None,
            poster=entry if isinstance(entry, Poster) else None,
            graphic=entry.graphic if isinstance(entry, Showing) else None,
            duration=entry.duration if isinstance(entry, Showing) else None,
            pinned=position < pinned,
        )
        for position, entry in enumerate(entries)
    )


def _fitting(
    grant: AirtimeGrant,
    items: Sequence[GrantItem],
    *,
    padded: bool,
    start: datetime | None = None,
    previous: Entry | None = None,
) -> list[GrantItem]:
    """The items that fit the window, in order, laid out without the ones
    that don't, so an entry left out leaves no hole. They follow
    `previous`, ending at `start` (the grant's start by default)."""
    kept: list[GrantItem] = []
    start = start or grant.starttime
    for item in items:
        entry = item.entry
        placed = layout.lay_out(
            start, [i.entry for i in kept] + [entry], padded=padded, previous=previous
        )
        if entry.duration > timedelta(0) and placed[-1].end <= grant.endtime:
            kept.append(item)
    return kept


def _key(entry: Entry) -> tuple[str, int]:
    if isinstance(entry, Showing):
        return ("graphic", entry.pk)
    return ("poster" if isinstance(entry, Poster) else "video", entry.pk)


def _fill(grant: AirtimeGrant, context: ScheduleContext, now: datetime) -> bool:
    """Fill `grant` around its pinned items. Returns whether the list changed."""
    assert grant.recipe is not None
    items = list(grant.items.select_related("video", "poster", "graphic"))
    pinned = [item.entry for item in items if item.pinned]
    picks = plan(grant.recipe, grant, context, now, pinned=pinned)
    if [_key(e) for e in picks] == [_key(item.entry) for item in items]:
        return False
    _replace_items(grant, picks, pinned=len(pinned))
    return True


def _context_for(refilling: Sequence[AirtimeGrant], now: datetime) -> ScheduleContext:
    """What is programmed from now to the issue horizon, apart from the
    grants about to be refilled: the compiled schedule and every other
    grant's list."""
    horizon = max([broadcast_time.issue_horizon(now), *(g.endtime for g in refilling)], default=now)
    context = ScheduleContext()
    context.refilling = {g.pk for g in refilling}
    rows = Scheduleitem.objects.overlapping(now, horizon).exclude(grant__in=context.refilling)
    for row in rows.select_related("video"):
        context.count(row.video, row.duration, compiled=True, at=row.starttime)
    others = (
        GrantItem.objects.filter(
            grant__starttime__lt=horizon, grant__endtime__gt=now, video__isnull=False
        )
        .exclude(grant__state__in=(State.CANCELLED, State.COMPILED))
        .exclude(grant__in=[g.pk for g in refilling])
        .select_related("video", "grant")
    )
    for item in others:
        context.count(item.video, item.video.duration, at=item.grant.starttime)
    return context
