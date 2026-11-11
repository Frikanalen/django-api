"""The service layer: the nightly pipeline, editing grants, the cut-over."""

import datetime
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings

from fk.models import LiveSource, LiveTransmission, Organization, Scheduleitem, User
from scheduling import broadcast_time, services
from scheduling.models import AirtimeGrant, GrantItem, RecurringSlot

from .conftest import NOW, OSLO

pytestmark = pytest.mark.django_db
State = AirtimeGrant.State

# Thursdays at 19:00 from NOW (Wed 2026-01-07) to the horizon (2026-01-26).
THIS_WEEK = datetime.datetime(2026, 1, 8, 19, tzinfo=OSLO)  # locked already
NEXT_WEEK = datetime.datetime(2026, 1, 15, 19, tzinfo=OSLO)  # locked already
OPEN_WEEK = datetime.datetime(2026, 1, 22, 19, tzinfo=OSLO)  # locks Mon 2026-01-12


def items(grant: AirtimeGrant) -> list[int]:
    return list(grant.items.order_by("position").values_list("video_id", flat=True))


def schedule(grant: AirtimeGrant) -> list[tuple]:
    return [
        (row.video_id, row.starttime, row.duration)
        for row in Scheduleitem.objects.filter(grant=grant).order_by("starttime")
    ]


# -- Constraints -------------------------------------------------------------


def test_grants_cannot_overlap(make_grant) -> None:
    make_grant(OPEN_WEEK)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_grant(OPEN_WEEK + timedelta(minutes=30))


def test_a_cancelled_grant_frees_its_window(make_grant) -> None:
    services.cancel_grant(make_grant(OPEN_WEEK))
    make_grant(OPEN_WEEK)


def test_item_positions_can_be_swapped_inside_a_transaction(make_grant, make_video) -> None:
    grant = make_grant(OPEN_WEEK)
    a = GrantItem.objects.create(grant=grant, position=0, video=make_video())
    b = GrantItem.objects.create(grant=grant, position=1, video=make_video())
    with transaction.atomic():
        GrantItem.objects.filter(pk=a.pk).update(position=1)
        GrantItem.objects.filter(pk=b.pk).update(position=0)


# -- Issue -------------------------------------------------------------------


def test_issue_creates_a_grant_per_occurrence_up_to_the_horizon(weekly_slot) -> None:
    services.issue_grants(NOW)

    grants = list(AirtimeGrant.objects.order_by("starttime"))
    assert [g.starttime for g in grants] == [THIS_WEEK, NEXT_WEEK, OPEN_WEEK]
    assert all(g.endtime - g.starttime == timedelta(hours=1) for g in grants)
    assert all(g.fill_rule == weekly_slot.default_fill_rule for g in grants)
    assert all(g.origin_slot == weekly_slot for g in grants)
    assert grants[2].locks_at == datetime.datetime(2026, 1, 12, tzinfo=OSLO)


def test_issue_is_idempotent_and_never_reissues_a_cancelled_occurrence(weekly_slot) -> None:
    services.issue_grants(NOW)
    services.cancel_grant(AirtimeGrant.objects.get(starttime=OPEN_WEEK))

    services.issue_grants(NOW)

    assert AirtimeGrant.objects.count() == 3
    assert AirtimeGrant.objects.get(starttime=OPEN_WEEK).state == State.CANCELLED


def test_issue_skips_a_window_already_granted_to_someone_else(weekly_slot, make_grant) -> None:
    other = Organization.objects.create(name="Other")
    make_grant(OPEN_WEEK - timedelta(minutes=30), organization=other)

    services.issue_grants(NOW)

    assert not AirtimeGrant.objects.filter(origin_slot=weekly_slot, starttime=OPEN_WEEK).exists()


def test_editing_a_slot_never_moves_an_issued_grant(weekly_slot) -> None:
    services.issue_grants(NOW)
    weekly_slot.start_time = datetime.time(20)
    weekly_slot.save()

    services.issue_grants(NOW)

    starts = set(AirtimeGrant.objects.values_list("starttime", flat=True))
    assert OPEN_WEEK in starts  # the issued grant kept its time


def test_unowned_slots_issue_to_the_station() -> None:
    station = Organization.objects.create(name="Frikanalen")
    slot = RecurringSlot.objects.create(
        weekdays=[3],
        start_time=datetime.time(13),
        duration=timedelta(hours=2),
        starts_on=datetime.date(2026, 1, 1),
    )
    with override_settings(STATION_ORGANIZATION_ID=station.pk):
        services.issue_grants(NOW)

    assert {g.organization for g in slot.grants.all()} == {station}


# -- Fill, lock, compile -----------------------------------------------------


def test_the_pipeline_compiles_locked_weeks_and_leaves_the_open_week_open(
    weekly_slot, make_video
) -> None:
    newest = make_video(25)

    services.run_nightly(NOW)

    this, nxt, open_ = AirtimeGrant.objects.order_by("starttime")
    assert (this.state, nxt.state, open_.state) == (State.COMPILED, State.COMPILED, State.OPEN)
    assert schedule(this) == [(newest.pk, THIS_WEEK, timedelta(minutes=25))]
    assert items(open_) == [newest.pk]
    assert not Scheduleitem.objects.filter(grant=open_).exists()
    # Compiled rows are compiler output: a grant, no historical reason.
    assert set(Scheduleitem.objects.values_list("schedulereason", flat=True)) == {None}


def test_compile_plays_items_back_to_back_from_the_start(make_grant, make_video) -> None:
    a, b = make_video(20), make_video(15)
    grant = make_grant(OPEN_WEEK, state=State.LOCKED)
    services._replace_items(grant, [a, b])

    assert services.compile_grant(grant)

    assert schedule(grant) == [
        (a.pk, OPEN_WEEK, timedelta(minutes=20)),
        (b.pk, OPEN_WEEK + timedelta(minutes=20), timedelta(minutes=15)),
    ]


def test_compile_refuses_a_window_something_else_occupies(make_grant, make_video) -> None:
    video = make_video(20)
    Scheduleitem.objects.create(
        video=video,
        starttime=OPEN_WEEK,
        duration=timedelta(minutes=5),
        schedulereason=Scheduleitem.REASON_JUKEBOX,
    )
    grant = make_grant(OPEN_WEEK, state=State.LOCKED)
    services._replace_items(grant, [video])

    assert not services.compile_grant(grant)
    grant.refresh_from_db()
    assert grant.state == State.LOCKED
    assert not Scheduleitem.objects.filter(grant=grant).exists()


def test_refill_only_writes_when_the_answer_changed(weekly_slot, make_video) -> None:
    make_video(25)
    services.issue_grants(NOW)
    services.fill_grants(NOW)
    open_ = AirtimeGrant.objects.get(starttime=OPEN_WEEK)
    first_item = open_.items.get()

    services.fill_grants(NOW)
    assert open_.items.get().pk == first_item.pk  # untouched

    newer = make_video(25)
    services.fill_grants(NOW)
    assert items(open_)[0] == newer.pk


def test_hand_edited_grants_are_not_refilled(weekly_slot, editor, make_video) -> None:
    make_video(10)
    services.issue_grants(NOW)
    open_ = AirtimeGrant.objects.get(starttime=OPEN_WEEK)
    chosen = make_video(30, created_time=datetime.datetime(2020, 1, 1, tzinfo=OSLO))
    services.set_items(open_, [chosen], user=editor, now=NOW)

    services.fill_grants(NOW)

    open_.refresh_from_db()
    assert open_.fill_rule is None
    assert items(open_) == [chosen.pk]


def test_lock_drops_unlistable_videos_but_keeps_pending_uploads(make_grant, make_video) -> None:
    gone, pending, fine = make_video(10), make_video(10, proper_import=False), make_video(10)
    grant = make_grant(OPEN_WEEK, locks_at=NOW - timedelta(minutes=1))
    services._replace_items(grant, [gone, pending, fine])
    gone.organization = Organization.objects.create(name="No editor any more")
    gone.save()

    services.lock_grants(NOW)

    grant.refresh_from_db()
    assert grant.state == State.LOCKED
    assert items(grant) == [pending.pk, fine.pk]


def test_an_empty_grant_compiles_to_nothing(make_grant) -> None:
    grant = make_grant(OPEN_WEEK, locks_at=NOW - timedelta(days=1))

    services.run_nightly(NOW)

    grant.refresh_from_db()
    assert grant.state == State.COMPILED
    assert not Scheduleitem.objects.exists()


def test_a_lock_exempt_grant_compiles_without_locking_and_stays_editable(
    make_grant, make_video, editor
) -> None:
    video = make_video(10)
    grant = make_grant(OPEN_WEEK, locks_at=NOW - timedelta(days=1), lock_exempt=True)
    services._replace_items(grant, [video])

    services.run_nightly(NOW)
    grant.refresh_from_db()
    assert grant.state == State.COMPILED
    assert services.can_edit(grant, editor, NOW)

    other = make_video(20)
    services.set_items(grant, [other], user=editor, now=NOW)

    assert schedule(grant) == [(other.pk, OPEN_WEEK, timedelta(minutes=20))]


def test_a_pending_upload_arriving_with_its_real_length_recompiles(
    make_grant, make_video, django_capture_on_commit_callbacks
) -> None:
    pending = make_video(30, proper_import=False)
    grant = make_grant(datetime.datetime.now(OSLO) + timedelta(days=3), state=State.LOCKED)
    services._replace_items(grant, [pending])
    services.compile_grant(grant)

    with django_capture_on_commit_callbacks(execute=True):
        pending.duration = timedelta(minutes=27)
        pending.proper_import = True
        pending.save()

    assert [d for _, _, d in schedule(grant)] == [timedelta(minutes=27)]


def test_a_file_too_long_for_the_window_is_left_out(make_grant, make_video) -> None:
    video = make_video(30, proper_import=False)
    grant = make_grant(OPEN_WEEK, minutes=30, state=State.LOCKED)
    services._replace_items(grant, [video])
    video.duration = timedelta(minutes=31)
    video.save()

    services.compile_grant(grant)

    assert schedule(grant) == []


def test_cancel_takes_compiled_rows_off_the_schedule(make_grant, make_video) -> None:
    grant = make_grant(OPEN_WEEK, state=State.LOCKED)
    services._replace_items(grant, [make_video()])
    services.compile_grant(grant)

    services.cancel_grant(grant, now=NOW)

    assert not Scheduleitem.objects.exists()


# -- Editing -----------------------------------------------------------------


def test_members_edit_while_open_and_staff_until_air(make_grant, editor) -> None:
    staff = User.objects.create(email="staff@example.test", is_superuser=True)
    grant = make_grant(OPEN_WEEK)
    assert services.can_edit(grant, editor, NOW)

    past_deadline = grant.locks_at + timedelta(seconds=1)
    assert not services.can_edit(grant, editor, past_deadline)
    grant.state = State.LOCKED
    assert services.can_edit(grant, staff, past_deadline)
    grant.state = State.COMPILED
    assert services.can_edit(grant, staff, past_deadline)
    assert not services.can_edit(grant, staff, grant.starttime)
    grant.state = State.CANCELLED
    assert not services.can_edit(grant, staff, past_deadline)


def test_staff_editing_a_compiled_grant_recompiles_it(make_grant, make_video) -> None:
    staff = User.objects.create(email="staff@example.test", is_superuser=True)
    grant = make_grant(OPEN_WEEK, locks_at=NOW - timedelta(days=1))
    services._replace_items(grant, [make_video(10)])
    services.run_nightly(NOW)
    grant.refresh_from_db()
    assert grant.state == State.COMPILED

    other = make_video(20)
    services.set_items(grant, [other], user=staff, now=NOW)

    assert schedule(grant) == [(other.pk, OPEN_WEEK, timedelta(minutes=20))]


def test_outsiders_and_unapproved_members_cannot_edit(make_grant, organization, editor) -> None:
    grant = make_grant(OPEN_WEEK)
    outsider = User.objects.create(email="outsider@example.test", identity_confirmed=True)
    assert not services.can_edit(grant, outsider, NOW)

    editor.identity_confirmed = False
    assert not services.can_edit(grant, editor, NOW)


def test_set_items_validates_fit_and_listability(make_grant, make_video, editor) -> None:
    grant = make_grant(OPEN_WEEK, minutes=30)

    with pytest.raises(ValidationError, match="longer than the window"):
        services.set_items(grant, [make_video(20), make_video(20)], user=editor, now=NOW)
    with pytest.raises(ValidationError, match="Not airable"):
        services.set_items(grant, [make_video(0)], user=editor, now=NOW)

    pending = make_video(25, proper_import=False)
    services.set_items(grant, [pending], user=editor, now=NOW)
    assert items(grant) == [pending.pk]


def test_set_items_may_list_another_organizations_video(make_grant, make_video, editor) -> None:
    owner = User.objects.create(email="their-editor@example.test")
    theirs = make_video(10, organization=Organization.objects.create(name="Them", editor=owner))

    services.set_items(make_grant(OPEN_WEEK), [theirs], user=editor, now=NOW)


def test_set_items_is_refused_after_the_deadline(make_grant, make_video, editor) -> None:
    grant = make_grant(OPEN_WEEK, locks_at=NOW)
    with pytest.raises(PermissionDenied):
        services.set_items(grant, [make_video()], user=editor, now=NOW)


def test_make_automatic_restores_the_slots_rule_and_fills(weekly_slot, make_video, editor) -> None:
    newest = make_video(10)
    services.issue_grants(NOW)
    open_ = AirtimeGrant.objects.get(starttime=OPEN_WEEK)
    services.set_items(open_, [], user=editor, now=NOW)

    services.make_automatic(open_, user=editor, now=NOW)

    open_.refresh_from_db()
    assert open_.fill_rule == weekly_slot.default_fill_rule
    assert items(open_) == [newest.pk]


def test_make_automatic_needs_a_slot(make_grant, editor) -> None:
    with pytest.raises(ValidationError, match="no fill rule"):
        services.make_automatic(make_grant(OPEN_WEEK), user=editor, now=NOW)


def test_create_grant_defaults_the_deadline(organization) -> None:
    grant = services.create_grant(
        organization=organization, starttime=OPEN_WEEK, endtime=OPEN_WEEK + timedelta(hours=1)
    )
    assert grant.locks_at == broadcast_time.default_locks_at(OPEN_WEEK)


# -- Cut-over ----------------------------------------------------------------


def test_cut_over_replaces_the_old_schedule_and_is_safe_to_rerun(weekly_slot, make_video) -> None:
    video = make_video(25)
    before = Scheduleitem.objects.create(
        video=video,
        starttime=NOW - timedelta(hours=2),
        duration=timedelta(hours=1),
        schedulereason=Scheduleitem.REASON_JUKEBOX,
    )
    for hours in range(0, 24 * 21, 2):
        Scheduleitem.objects.create(
            video=video,
            starttime=NOW + timedelta(hours=hours),
            duration=timedelta(hours=2),
            schedulereason=Scheduleitem.REASON_JUKEBOX,
        )

    services.cut_over(NOW)
    compiled = set(Scheduleitem.objects.values_list("pk", flat=True))

    assert Scheduleitem.objects.filter(schedulereason__isnull=False).get() == before
    assert Scheduleitem.objects.filter(grant__isnull=False).count() == 2  # this and next week
    services.cut_over(NOW)
    assert set(Scheduleitem.objects.values_list("pk", flat=True)) == compiled


def test_unowned_slots_are_skipped_when_the_station_is_missing() -> None:
    RecurringSlot.objects.create(
        weekdays=[3],
        start_time=datetime.time(13),
        duration=timedelta(hours=2),
        starts_on=datetime.date(2026, 1, 1),
    )
    with override_settings(STATION_ORGANIZATION_ID=999_999):
        services.issue_grants(NOW)

    assert not AirtimeGrant.objects.exists()


# -- Live transmissions ------------------------------------------------------


def test_a_live_entry_compiles_to_a_live_schedule_item(
    make_grant, make_video, organization, editor
) -> None:
    source = LiveSource.objects.create(organization=organization, name="Studio")
    video = make_video(10)
    grant = make_grant(OPEN_WEEK, locks_at=NOW - timedelta(days=1), lock_exempt=True)
    services.set_items(
        grant,
        [services.LiveEntry(source, timedelta(minutes=30), "Debatt"), video],
        user=editor,
        now=NOW,
    )
    assert grant.items_duration == timedelta(minutes=40)

    services.run_nightly(NOW)

    assert schedule(grant) == [
        (None, OPEN_WEEK, timedelta(minutes=30)),
        (video.pk, OPEN_WEEK + timedelta(minutes=30), timedelta(minutes=10)),
    ]
    live = Scheduleitem.objects.get(grant=grant, video=None)
    assert (live.default_name, live.is_live) == ("Debatt", True)
    assert LiveTransmission.objects.get(schedule_item=live).source == source


def test_a_live_entry_survives_the_lock(make_grant, organization) -> None:
    source = LiveSource.objects.create(organization=organization, name="Studio")
    grant = make_grant(OPEN_WEEK, locks_at=NOW - timedelta(days=1))
    services._replace_items(grant, [services.LiveEntry(source, timedelta(minutes=20))])

    services.lock_grants(NOW)

    item = grant.items.get()
    assert (item.live_source, item.duration, item.name) == (source, timedelta(minutes=20), "Studio")


def test_a_live_entry_must_be_the_organizations_own_and_fit(
    make_grant, organization, editor
) -> None:
    other = Organization.objects.create(name="Other", fkmember=True)
    theirs = LiveSource.objects.create(organization=other, name="Theirs")
    ours = LiveSource.objects.create(organization=organization, name="Ours")
    grant = make_grant(OPEN_WEEK)

    with pytest.raises(ValidationError, match="another organization"):
        services.set_items(
            grant, [services.LiveEntry(theirs, timedelta(minutes=10))], user=editor, now=NOW
        )
    with pytest.raises(ValidationError, match="longer than the window"):
        services.set_items(
            grant, [services.LiveEntry(ours, timedelta(minutes=61))], user=editor, now=NOW
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        GrantItem.objects.create(grant=grant, position=0, live_source=ours)
