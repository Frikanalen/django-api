"""Every state change of grants, slots and the compiled schedule.

Models hold data and constraints; the API, the admin and the nightly job
all call these functions, so none of them can bypass the rules. Each
function runs in a transaction.

The nightly pipeline (:func:`run_nightly`) runs four steps with one
`now`, all safe to re-run:

1. issue  -- each recurring slot occurrence up to the end of the week
   after next becomes a grant, with the slot's default fill rule;
2. fill   -- every open grant with a fill rule is filled again, in
   chronological order with one shared ScheduleContext;
3. lock   -- open, non-exempt grants past their deadline lock; items
   that have become unlistable are dropped;
4. compile -- locked grants (and exempt ones past their deadline) are
   written to Scheduleitem back-to-back from their start.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from fk.models import LiveSource, LiveTransmission, Organization, Scheduleitem, Video
from scheduling import broadcast_time
from scheduling.filling import plan
from scheduling.models import AirtimeGrant, FillRule, GrantItem, RecurringSlot
from scheduling.selection import ScheduleContext

logger = logging.getLogger(__name__)

State = AirtimeGrant.State


@dataclass(frozen=True)
class LiveEntry:
    """A live transmission to list in a grant: a source, how long, a title."""

    source: LiveSource
    duration: timedelta
    title: str = ""

    @property
    def name(self) -> str:
        return self.title or self.source.name


# What a grant's list holds, in playing order.
Entry = Video | LiveEntry


def _length(entry: Entry) -> timedelta:
    return entry.duration


def _entry(item: GrantItem) -> Entry:
    if item.live_source_id:
        return LiveEntry(item.live_source, item.duration, item.title)
    return item.video


def station_organization_id() -> int:
    return settings.STATION_ORGANIZATION_ID


# -- Who may do what ---------------------------------------------------------


def may_act_for(user, organization: Organization) -> bool:
    """Whether `user` may program airtime on `organization`'s behalf.

    Staff always; otherwise a member or the editor of an approved
    Frikanalen member organization, with a confirmed identity.
    """
    if not user.is_authenticated:
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


def validate_items(grant: AirtimeGrant, entries: Sequence[Entry]) -> None:
    """Local to the grant: it owns its window, so there are no airtime
    conflicts to check -- only that everything is listable and fits."""
    videos = [e for e in entries if isinstance(e, Video)]
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
    for live in (e for e in entries if isinstance(e, LiveEntry)):
        if live.source.organization_id != grant.organization_id:
            raise ValidationError(
                {"items": f"The live source '{live.source}' belongs to another organization."}
            )
        if not live.source.enabled:
            raise ValidationError({"items": f"The live source '{live.source}' is disabled."})
        if live.duration <= timedelta(0):
            raise ValidationError({"items": f"The live transmission '{live.name}' needs a length."})
    total = sum((_length(e) for e in entries), timedelta(0))
    if total > grant.duration:
        raise ValidationError(
            {"items": f"The list runs {total}, which is longer than the window ({grant.duration})."}
        )


def set_items(
    grant: AirtimeGrant, entries: Sequence[Entry], *, user, now: datetime | None = None
) -> AirtimeGrant:
    """Replace the whole list, as the organization (or staff) edits it.

    Editing the list clears the fill rule: the grant is now hand-made. A
    lock-exempt grant that is already compiled is recompiled at once.
    """
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        if not can_edit(grant, user, now):
            raise PermissionDenied("This grant can no longer be edited.")
        validate_items(grant, entries)
        _replace_items(grant, entries)
        grant.fill_rule = None
        grant.save(update_fields=["fill_rule", "updated_at"])
        if grant.state == State.COMPILED:
            compile_grant(grant)
    return grant


def make_automatic(grant: AirtimeGrant, *, user, now: datetime | None = None) -> AirtimeGrant:
    """Switch a grant back to its slot's default fill rule, and fill it now."""
    now = now or timezone.now()
    with transaction.atomic():
        grant = AirtimeGrant.objects.select_for_update().get(pk=grant.pk)
        if not can_edit(grant, user, now):
            raise PermissionDenied("This grant can no longer be edited.")
        rule = grant.origin_slot.default_fill_rule if grant.origin_slot else None
        if rule is None:
            raise ValidationError({"fill_rule": "This grant has no fill rule to go back to."})
        grant.fill_rule = rule
        grant.save(update_fields=["fill_rule", "updated_at"])
        _fill(grant, _context_for([grant], now), now)
        if grant.state == State.COMPILED:
            compile_grant(grant)
    return grant


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
    newest uploads, filling the window. Doing nothing still works."""
    return FillRule.objects.create(
        organization=organization,
        name=f"Newest from {organization.name}"[:100],
        pool=FillRule.Pool.ORGANIZATION,
        ordering=FillRule.Ordering.LATEST,
        mode=FillRule.Mode.FILL,
    )


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
    for slot in RecurringSlot.objects.select_related("default_fill_rule"):
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


def fill_grants(now: datetime) -> None:
    """Refill every open grant that has a fill rule, oldest first, with one
    shared context. Items change only when the answer did."""
    grants = list(
        AirtimeGrant.objects.filter(state=State.OPEN, fill_rule__isnull=False)
        .select_related("fill_rule")
        .order_by("starttime")
    )
    context = _context_for(grants, now)
    for grant in grants:
        with transaction.atomic():
            _fill(grant, context, now)


def lock_grants(now: datetime) -> None:
    """Lock open, non-exempt grants whose deadline has passed. Items that
    can no longer be listed are dropped; pending uploads stay."""
    due = AirtimeGrant.objects.filter(state=State.OPEN, lock_exempt=False, locks_at__lte=now)
    for grant in due:
        with transaction.atomic():
            items = list(grant.items.select_related("video", "live_source"))
            listable = set(
                Video.objects.listable()
                .filter(pk__in=[i.video_id for i in items if i.video_id])
                .values_list("pk", flat=True)
            )
            # Live entries have nothing to become unlistable.
            dropped = [i.video_id for i in items if i.video_id and i.video_id not in listable]
            kept = [_entry(i) for i in items if i.video_id not in dropped]
            if dropped:
                logger.warning("Dropping unlistable videos %s from %s at lock", dropped, grant)
                _replace_items(grant, kept)
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
    """
    try:
        with transaction.atomic():
            grant.schedule_items.all().delete()
            blocking = Scheduleitem.objects.overlapping(grant.starttime, grant.endtime).first()
            if blocking is not None:
                raise _Blocked(blocking)
            cursor = grant.starttime
            for item in grant.items.select_related("video", "live_source"):
                length = item.length
                if length <= timedelta(0) or cursor + length > grant.endtime:
                    logger.warning(
                        "Leaving %s out of %s: it no longer fits the window", item.name, grant
                    )
                    continue
                if item.live_source_id:
                    row = Scheduleitem.objects.create(
                        default_name=item.name,
                        is_live=True,
                        starttime=cursor,
                        duration=length,
                        grant=grant,
                    )
                    LiveTransmission.objects.create(schedule_item=row, source=item.live_source)
                else:
                    Scheduleitem.objects.create(
                        video=item.video, starttime=cursor, duration=length, grant=grant
                    )
                cursor += length
            grant.state = State.COMPILED
            grant.save(update_fields=["state", "updated_at"])
    except _Blocked as blocked:
        logger.error("Cannot compile %s: the window holds %s", grant, blocked.item)
        return False
    return True


def recompile_for_video(video: Video, now: datetime | None = None) -> None:
    """A pending upload's file arrived with its real length: recompile the
    compiled grants yet to air whose rows no longer match it. A save
    that changes nothing about the length leaves the schedule alone."""
    now = now or timezone.now()
    grants = AirtimeGrant.objects.filter(
        state=State.COMPILED, starttime__gt=now, items__video=video
    ).distinct()
    for grant in grants:
        rows = list(grant.schedule_items.filter(video=video).values_list("duration", flat=True))
        listed = grant.items.filter(video=video).count()
        if len(rows) != listed or any(d != video.duration for d in rows):
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


def _replace_items(grant: AirtimeGrant, entries: Sequence[Entry]) -> None:
    grant.items.all().delete()
    GrantItem.objects.bulk_create(
        GrantItem(
            grant=grant,
            position=position,
            live_source=entry.source,
            duration=entry.duration,
            title=entry.title,
        )
        if isinstance(entry, LiveEntry)
        else GrantItem(grant=grant, position=position, video=entry)
        for position, entry in enumerate(entries)
    )


def _fill(grant: AirtimeGrant, context: ScheduleContext, now: datetime) -> None:
    assert grant.fill_rule is not None
    picks = plan(grant.fill_rule, grant, context, now)
    current = list(grant.items.values_list("video_id", flat=True))
    if [v.pk for v in picks] != current:
        _replace_items(grant, picks)


def _context_for(refilling: Sequence[AirtimeGrant], now: datetime) -> ScheduleContext:
    """What is programmed from now to the issue horizon, apart from the
    grants about to be refilled: the compiled schedule and every other
    grant's list."""
    horizon = max([broadcast_time.issue_horizon(now), *(g.endtime for g in refilling)], default=now)
    context = ScheduleContext()
    for row in Scheduleitem.objects.overlapping(now, horizon).select_related("video"):
        context.count(row.video, row.duration, compiled=True)
    others = (
        GrantItem.objects.filter(grant__starttime__lt=horizon, grant__endtime__gt=now)
        .exclude(grant__state__in=(State.CANCELLED, State.COMPILED))
        .exclude(grant__in=[g.pk for g in refilling])
        .filter(video__isnull=False)
        .select_related("video")
    )
    for item in others:
        context.count(item.video, item.video.duration)
    return context
