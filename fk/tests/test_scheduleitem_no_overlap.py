"""
The no-overlap constraint on Scheduleitem, and how validation reports it.

Rows written under airtime grants -- by the compiler or by staff -- carry
no schedule reason and may not overlap; the database refuses them, and
full_clean() (so the admin) reports it as a validation error first.
Historical rows carry a reason and are exempt: years of unvalidated
writes left overlapping pairs that must not block the constraint or the
editing of those rows.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from fk.models import Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db

START = timezone.now() + timedelta(days=1)
MESSAGE = "Another programme is scheduled at that time."


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="overlap-editor@example.test")
    organization = Organization.objects.create(name="Overlap org", editor=editor)
    return Video.objects.create(
        name="Overlap video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=30),
        proper_import=True,
    )


def make_item(video: Video, start, minutes: int, schedulereason=None) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video,
        schedulereason=schedulereason,
        starttime=start,
        duration=timedelta(minutes=minutes),
    )


def unsaved(video: Video, start, minutes: int) -> Scheduleitem:
    return Scheduleitem(video=video, starttime=start, duration=timedelta(minutes=minutes))


@pytest.mark.parametrize(
    ("offset", "minutes"),
    [
        pytest.param(10, 10, id="starts-inside"),
        pytest.param(-10, 20, id="runs-into"),
        pytest.param(-10, 60, id="contains"),
    ],
)
def test_validation_refuses_an_overlapping_entry(video: Video, offset: int, minutes: int) -> None:
    make_item(video, START, 30)

    with pytest.raises(ValidationError) as excinfo:
        unsaved(video, START + timedelta(minutes=offset), minutes).full_clean()
    assert excinfo.value.messages == [MESSAGE]


def test_the_database_refuses_it_too(video: Video) -> None:
    make_item(video, START, 30)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_item(video, START + timedelta(minutes=10), 10)


def test_allows_back_to_back_items(video: Video) -> None:
    """Half-open bounds: ending exactly where the next begins is not a clash."""
    make_item(video, START, 30)

    unsaved(video, START + timedelta(minutes=30), 10).full_clean()


def test_historical_rows_may_overlap_and_stay_editable(video: Video) -> None:
    make_item(video, START, 30, schedulereason=Scheduleitem.REASON_JUKEBOX)
    overlapping = make_item(video, START + timedelta(minutes=10), 10, Scheduleitem.REASON_USER)

    overlapping.default_name = "Renamed, timing untouched"
    overlapping.full_clean()
    overlapping.save()


def test_rechecks_when_an_existing_item_is_moved(video: Video) -> None:
    make_item(video, START, 30)
    mover = make_item(video, START + timedelta(hours=2), 10)

    mover.starttime = START + timedelta(minutes=5)

    with pytest.raises(ValidationError):
        mover.full_clean()


def test_an_item_does_not_conflict_with_itself(video: Video) -> None:
    item = make_item(video, START, 30)

    item.duration = timedelta(minutes=20)

    item.full_clean()


def test_zero_length_items_occupy_no_airtime(video: Video) -> None:
    """Range semantics: an empty interval cannot intersect anything."""
    make_item(video, START, 30)

    unsaved(video, START + timedelta(minutes=10), 0).full_clean()
