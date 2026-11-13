"""Every state change of grants, slots and the compiled schedule.

Models hold data and constraints; the API, the admin and the nightly job
all call these functions, so none of them can bypass the rules. Each
function runs in a transaction.

The nightly pipeline (:func:`run_nightly`) runs four steps with one
`now`, all safe to re-run:

1. issue  -- each recurring slot occurrence up to the end of the week
   after next becomes a grant, with the slot's default fill rule;
2. fill   -- every grant with a fill rule is filled again around what
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
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from fk.models import Organization, Scheduleitem, Series, Video
from scheduling import broadcast_time
from scheduling.filling import plan
from scheduling.models import AirtimeGrant, FillRule, GrantItem, RecurringSlot
from scheduling.selection import ScheduleContext

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
    lock-exempt grant. Staff: until air, whatever the state; saving a
    compiled grant recompiles it.
    """
    now = now or timezone.now()
    if grant.state == State.CANCELLED or not may_act_for(user, grant.organization):
        return False
    if grant.lock_exempt or user.is_staff:
        return grant.starttime > now
    return grant.state == State.OPEN and grant.locks_at > now


# -- Editing a grant ---------------------------------------------------------


def validate_items(grant: AirtimeGrant, videos: Sequence[Video]) -> None:
    """Local to the grant: it owns its window, so there are no airtime
    conflicts to check -- only that everything is listable and fits."""
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
    total = sum((v.duration for v in videos), timedelta(0))
    if total > grant.duration:
        raise ValidationError(
            {"items": f"The list runs {total}, which is longer than the window ({grant.duration})."}
        )


def set_items(
    grant: AirtimeGrant,
    videos: Sequence[Video],
    *,
    user,
    now: datetime | None = None,
    automatic: bool = False,
) -> AirtimeGrant:
    """Save the list the organization (or staff) chose. Every video given
    is pinned.

    With `automatic`, the grant's rule fills the time the pinned videos
    leave, now and on every later run. Without it the grant is hand-made:
    the rule is cleared and the list stays exactly as saved. A grant that
    is already compiled is recompiled at once.
    """
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        if not can_edit(grant, user, now):
            raise PermissionDenied("This grant can no longer be edited.")
        validate_items(grant, videos)
        _replace_items(grant, videos, pinned=len(videos))
        grant.fill_rule = rule_for(grant) if automatic else None
        grant.save(update_fields=["fill_rule", "updated_at"])
        if automatic:
            _fill(grant, _context_for([grant], now), now)
        if grant.state == State.COMPILED:
            compile_grant(grant)
    return grant


def make_automatic(grant: AirtimeGrant, *, user, now: datetime | None = None) -> AirtimeGrant:
    """Unpin everything and let the grant's rule program the whole window."""
    return set_items(grant, [], user=user, now=now, automatic=True)


def rule_for(grant: AirtimeGrant) -> FillRule:
    """The rule an automatic grant fills by: the one it has, else its
    slot's, else the owner's standard one."""
    if grant.fill_rule is not None:
        return grant.fill_rule
    slot_rule = grant.origin_slot.default_fill_rule if grant.origin_slot else None
    return slot_rule or standard_fill_rule(grant.organization)


def create_grant(
    *,
    organization: Organization,
    starttime: datetime,
    endtime: datetime,
    locks_at: datetime | None = None,
    lock_exempt: bool = False,
    fill_rule: FillRule | None = None,
) -> AirtimeGrant:
    """Staff gives an organization a window directly."""
    grant = AirtimeGrant(
        organization=organization,
        starttime=starttime,
        endtime=endtime,
        locks_at=locks_at or broadcast_time.default_locks_at(starttime),
        lock_exempt=lock_exempt,
        fill_rule=fill_rule,
    )
    grant.full_clean()
    with transaction.atomic():
        grant.save()
    return grant


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
            fill_rule=standard_fill_rule(organization),
        )
    except IntegrityError as error:  # lost a race for the window
        raise ValidationError({"starttime": "Someone else got this airtime first."}) from error


def update_grant(grant: AirtimeGrant, **changes) -> AirtimeGrant:
    """Staff changes a grant's deadline or lock exemption."""
    allowed = {"locks_at", "lock_exempt"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationError({field: "Cannot be changed." for field in unknown})
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        for field, value in changes.items():
            setattr(grant, field, value)
        grant.save(update_fields=[*changes, "updated_at"])
    return grant


def cancel_grant(grant: AirtimeGrant, *, now: datetime | None = None) -> AirtimeGrant:
    """Withdraw a grant, freeing its window. Whatever it compiled that has
    not aired yet comes off the schedule."""
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        grant.schedule_items.filter(starttime__gte=now).delete()
        grant.state = State.CANCELLED
        grant.save(update_fields=["state", "updated_at"])
    return grant


def default_fill_rule(organization: Organization) -> FillRule:
    """The rule a newly approved or transferred slot gets: the owner's
    new uploads first, then their archive, least aired first, filling
    the window. Doing nothing still works, and does not repeat weekly."""
    return FillRule.objects.create(
        organization=organization,
        name=f"New, then archive, from {organization.name}"[:100],
        pool=FillRule.Pool.ORGANIZATION,
        ordering=FillRule.Ordering.PREMIERE,
        mode=FillRule.Mode.FILL,
    )


def standard_fill_rule(organization: Organization) -> FillRule:
    """The owner's new uploads, then their archive, filling the window:
    an existing rule like that if the organization has one, otherwise a
    new one."""
    existing = FillRule.objects.filter(
        organization=organization,
        pool=FillRule.Pool.ORGANIZATION,
        ordering=FillRule.Ordering.PREMIERE,
        mode=FillRule.Mode.FILL,
    ).first()
    return existing or default_fill_rule(organization)


def series_fill_rule(series: Series) -> FillRule:
    """The series' newest episode not yet aired first, then its earlier
    episodes, least aired first: an existing rule like that, otherwise a
    new one."""
    rule, _ = FillRule.objects.get_or_create(
        organization=series.organization,
        pool=FillRule.Pool.SERIES,
        series=series,
        ordering=FillRule.Ordering.PREMIERE,
        mode=FillRule.Mode.FILL,
        defaults={"name": f"Series: {series.name}"[:100]},
    )
    return rule


def set_slot_series(
    slot: RecurringSlot, series: Series | None, *, user, now: datetime | None = None
) -> RecurringSlot:
    """The owner points a slot at one of their series, or (None) back at
    everything they upload.

    The grants the slot already issued follow, as long as they still fill
    themselves and are not past the point where automatic grants stop
    changing; hand-made lists are left alone.
    """
    now = now or timezone.now()
    owner = slot.organization
    if owner is None or not may_act_for(user, owner):
        raise PermissionDenied("You can't program this slot.")
    if series is not None and series.organization_id != owner.pk:
        raise ValidationError({"series": "The series must belong to the slot's organization."})
    with transaction.atomic():
        rule = series_fill_rule(series) if series else standard_fill_rule(owner)
        slot.default_fill_rule = rule
        slot.save(update_fields=["default_fill_rule"])
        following = list(
            slot.grants.filter(fill_rule__isnull=False)
            .exclude(fill_rule=rule)
            .filter(
                Q(state=State.OPEN)
                | Q(state__in=(State.LOCKED, State.COMPILED), starttime__gt=now + REFRESH_UNTIL)
            )
            .filter(starttime__gt=now)
            .order_by("starttime")
        )
        context = _context_for(following, now)
        for grant in following:
            grant.fill_rule = rule
            grant.save(update_fields=["fill_rule", "updated_at"])
            if _fill(grant, context, now) and grant.state == State.COMPILED:
                compile_grant(grant)
    return slot


def jukebox_fill_rule() -> FillRule:
    """The video jukebox: a weighted draw from the filler pool, filling
    the window. One rule, owned by the station, shared by every jukebox
    slot."""
    rule, _ = FillRule.objects.get_or_create(
        organization_id=station_organization_id(),
        pool=FillRule.Pool.FILLER,
        ordering=FillRule.Ordering.WEIGHTED,
        mode=FillRule.Mode.FILL,
        defaults={"name": "Video jukebox"},
    )
    return rule


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
    """A weekly slot from an approved creation request, with a default rule."""
    return RecurringSlot.objects.create(
        organization=organization,
        frequency=RecurringSlot.Frequency.WEEKLY,
        weekdays=[day],
        start_time=start_time,
        duration=duration,
        starts_on=broadcast_time.local_date(timezone.now()),
        default_fill_rule=default_fill_rule(organization),
    )


def transfer_slot(slot: RecurringSlot, organization: Organization) -> None:
    """Give a slot to a new owner. The old owner's rule stays with them;
    the new owner gets a default one. Issued grants keep their owner."""
    slot.organization = organization
    slot.default_fill_rule = default_fill_rule(organization)
    slot.save(update_fields=["organization", "default_fill_rule"])


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
    slots = RecurringSlot.objects.select_related("default_fill_rule").exclude(
        organization__is_rogue=True
    )
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
                fill_rule=slot.default_fill_rule,
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
    """Refill every grant that has a fill rule, oldest first, with one
    shared context: open ones, and locked or compiled ones still more
    than REFRESH_UNTIL from air. Items change only when the answer did;
    a compiled grant whose answer changed is recompiled."""
    grants = list(
        AirtimeGrant.objects.filter(fill_rule__isnull=False)
        .filter(
            Q(state=State.OPEN)
            | Q(state__in=(State.LOCKED, State.COMPILED), starttime__gt=now + REFRESH_UNTIL)
        )
        .select_related("fill_rule")
        .order_by("starttime")
    )
    context = _context_for(grants, now)
    for grant in grants:
        with transaction.atomic():
            if _fill(grant, context, now) and grant.state == State.COMPILED:
                compile_grant(grant)


def lock_grants(now: datetime) -> None:
    """Lock open, non-exempt grants whose deadline has passed. Items that
    can no longer be listed are dropped; pending uploads stay."""
    due = AirtimeGrant.objects.filter(state=State.OPEN, lock_exempt=False, locks_at__lte=now)
    for grant in due:
        with transaction.atomic():
            items = list(grant.items.select_related("video"))
            listable = set(
                Video.objects.listable()
                .filter(pk__in=[i.video_id for i in items])
                .values_list("pk", flat=True)
            )
            kept = [i for i in items if i.video_id in listable]
            if len(kept) != len(items):
                dropped = [i.video_id for i in items if i.video_id not in listable]
                logger.warning("Dropping unlistable videos %s from %s at lock", dropped, grant)
                _replace_items(grant, [i.video for i in kept], pinned=sum(i.pinned for i in kept))
            grant.state = State.LOCKED
            grant.save(update_fields=["state", "updated_at"])


def compile_grants(now: datetime) -> None:
    due = AirtimeGrant.objects.filter(
        Q(state=State.LOCKED) | Q(state=State.OPEN, lock_exempt=True, locks_at__lte=now)
    ).order_by("starttime")
    for grant in due:
        compile_grant(grant)


def compile_grant(grant: AirtimeGrant) -> bool:
    """Write the grant's items to Scheduleitem back-to-back from its start,
    replacing whatever it compiled before. Returns whether it did.

    A grant owns its window, so normally nothing else is there. If
    something is -- a staff entry, or a row from before the cut-over --
    the grant is left as it was and the conflict logged.

    A suspended (is_rogue) member's grant compiles to nothing: what it
    put on the schedule and has not aired yet comes off, and the grant
    is left in its state.
    """
    if grant.organization.is_rogue:
        grant.schedule_items.filter(starttime__gt=timezone.now()).delete()
        logger.warning("Not compiling %s: %s is suspended", grant, grant.organization)
        return False
    try:
        with transaction.atomic():
            grant.schedule_items.all().delete()
            blocking = Scheduleitem.objects.overlapping(grant.starttime, grant.endtime).first()
            if blocking is not None:
                raise _Blocked(blocking)
            cursor = grant.starttime
            for item in grant.items.select_related("video__live_transmission"):
                length = item.video.duration
                if length <= timedelta(0) or cursor + length > grant.endtime:
                    logger.warning(
                        "Leaving %s out of %s: it no longer fits the window", item.video, grant
                    )
                    continue
                Scheduleitem.objects.create(
                    video=item.video,
                    starttime=cursor,
                    duration=length,
                    grant=grant,
                    is_live=airs_live(item.video),
                )
                cursor += length
            grant.state = State.COMPILED
            grant.save(update_fields=["state", "updated_at"])
    except _Blocked as blocked:
        logger.error("Cannot compile %s: the window holds %s", grant, blocked.item)
        return False
    return True


def recompile_for_video(video: Video, now: datetime | None = None) -> None:
    """A pending upload's file arrived with its real length, or a live
    programme got its recording: recompile the compiled grants yet to air
    whose rows no longer match it. A save that changes nothing about the
    length or how it airs leaves the schedule alone."""
    now = now or timezone.now()
    # Fresh, so a live transmission just added or removed is seen.
    video = Video.objects.select_related("live_transmission").get(pk=video.pk)
    live = airs_live(video)
    grants = AirtimeGrant.objects.filter(
        state=State.COMPILED, starttime__gt=now, items__video=video
    ).distinct()
    # Staff entries have no grant to recompile; they only follow how it airs.
    Scheduleitem.objects.filter(video=video, grant=None, starttime__gt=now).exclude(
        is_live=live
    ).update(is_live=live)
    for grant in grants:
        rows = list(grant.schedule_items.filter(video=video).values_list("duration", "is_live"))
        listed = grant.items.filter(video=video).count()
        if len(rows) != listed or any(d != video.duration or l != live for d, l in rows):
            compile_grant(grant)


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


def _replace_items(grant: AirtimeGrant, videos: Sequence[Video], pinned: int = 0) -> None:
    """Pinned items lead the list, so the first `pinned` videos are the
    pinned ones."""
    grant.items.all().delete()
    GrantItem.objects.bulk_create(
        GrantItem(grant=grant, position=position, video=video, pinned=position < pinned)
        for position, video in enumerate(videos)
    )


def _fill(grant: AirtimeGrant, context: ScheduleContext, now: datetime) -> bool:
    """Fill `grant` around its pinned items. Returns whether the list changed."""
    assert grant.fill_rule is not None
    items = list(grant.items.select_related("video"))
    pinned = [item.video for item in items if item.pinned]
    picks = plan(grant.fill_rule, grant, context, now, pinned=pinned)
    if [v.pk for v in picks] == [item.video_id for item in items]:
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
        context.count(row.video, row.duration, compiled=True)
    others = (
        GrantItem.objects.filter(grant__starttime__lt=horizon, grant__endtime__gt=now)
        .exclude(grant__state__in=(State.CANCELLED, State.COMPILED))
        .exclude(grant__in=[g.pk for g in refilling])
        .select_related("video")
    )
    for item in others:
        context.count(item.video, item.video.duration)
    return context
