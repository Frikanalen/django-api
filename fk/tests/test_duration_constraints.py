"""
Durations may not be negative, on any of the three models that carry one.

A negative length is not a shorter programme, it is corrupt data, and
every scheduler in the codebase does arithmetic on these fields: the
jukebox filler walked its clock backwards and looped forever on one, and
a Scheduleitem whose airtime ends before it starts is invisible to
the gap search that is supposed to route around it.

Two layers enforce it, and both are tested here because they catch
different things.  The validators give the API a 400 instead of a 500;
the database constraints are what actually hold, since `objects.create`
and `queryset.update` never run model validation.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from fk.models import Organization, Scheduleitem, User, Video, WeeklySlot

pytestmark = pytest.mark.django_db

NEGATIVE = timedelta(minutes=-5)


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="duration-editor@example.test")


@pytest.fixture
def organization(editor: User) -> Organization:
    return Organization.objects.create(name="Duration org", fkmember=True, editor=editor)


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Duration video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=30),
        proper_import=True,
    )


def test_video_duration_cannot_be_negative(editor: User, organization: Organization) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Video.objects.create(
            name="Negative video",
            creator=editor,
            organization=organization,
            duration=NEGATIVE,
            proper_import=True,
        )


def test_scheduleitem_duration_cannot_be_negative(video: Video) -> None:
    """Refused by the generated `airtime` column, not the check constraint.

    Postgres computes a generated column while building the row, so the
    tstzrange constructor rejects the inverted bounds before
    `scheduleitem_duration_not_negative` is ever evaluated. The row is
    still refused either way; only the error differs, and the field's
    MinValueValidator is what keeps that off the API surface.
    """
    with pytest.raises(DataError), transaction.atomic():
        Scheduleitem.objects.create(
            video=video,
            starttime=timezone.now(),
            duration=NEGATIVE,
            schedulereason=Scheduleitem.REASON_ADMIN,
        )


def test_weeklyslot_duration_cannot_be_negative() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        WeeklySlot.objects.create(day=0, start_time="12:00", duration=NEGATIVE)


def test_an_existing_row_cannot_be_updated_to_a_negative_duration(video: Video) -> None:
    """`update()` bypasses save() and full_clean() alike, so only the constraint stops it."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Video.objects.filter(pk=video.pk).update(duration=NEGATIVE)


def test_zero_duration_is_still_allowed(editor: User, organization: Organization) -> None:
    """
    The field defaults to zero and unimported videos rely on it; only
    negatives are rejected.  The jukebox screens zero out separately,
    because it cannot advance a clock either.
    """
    zero = Video.objects.create(
        name="Zero video",
        creator=editor,
        organization=organization,
        duration=timedelta(0),
    )

    assert zero.duration == timedelta(0)


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        pytest.param(Video, {"name": "Validated video"}, id="video"),
        pytest.param(WeeklySlot, {"day": 0, "start_time": "12:00"}, id="weeklyslot"),
    ],
)
def test_model_validation_rejects_a_negative_duration(model, fields: dict) -> None:
    """
    The validator is what turns this into a 400 at the API and admin,
    rather than letting the request reach the database and 500.
    """
    with pytest.raises(ValidationError) as raised:
        model(duration=NEGATIVE, **fields).full_clean()

    assert "duration" in raised.value.error_dict
