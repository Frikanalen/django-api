"""
Scheduleitem.clean() is what stands between the Django admin and a
double-booked channel: ScheduleitemAdmin is a bare ModelAdmin, so before
this the only overlap check in the codebase was the DRF serializer, which
the admin never touches.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from fk.models import Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db

START = timezone.now() + timedelta(days=1)


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="clean-editor@example.test")
    organization = Organization.objects.create(name="Clean org", editor=editor)
    return Video.objects.create(
        name="Clean video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=30),
        proper_import=True,
    )


def make_item(video: Video, start, minutes: int, **fields) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video,
        schedulereason=fields.pop("schedulereason", Scheduleitem.REASON_ADMIN),
        starttime=start,
        duration=timedelta(minutes=minutes),
    )


def test_rejects_an_item_starting_inside_another(video: Video) -> None:
    make_item(video, START, 30)

    clashing = Scheduleitem(
        video=video,
        schedulereason=Scheduleitem.REASON_ADMIN,
        starttime=START + timedelta(minutes=10),
        duration=timedelta(minutes=10),
    )

    with pytest.raises(ValidationError) as excinfo:
        clashing.full_clean()
    assert "duration" in excinfo.value.error_dict


def test_rejects_an_item_that_an_earlier_one_overruns_into(video: Video) -> None:
    """The asymmetric case: the conflict starts before the new item does."""
    make_item(video, START, 30)

    clashing = Scheduleitem(
        video=video,
        schedulereason=Scheduleitem.REASON_ADMIN,
        starttime=START + timedelta(minutes=20),
        duration=timedelta(minutes=10),
    )

    with pytest.raises(ValidationError) as excinfo:
        clashing.full_clean()
    assert "duration" in excinfo.value.error_dict


def test_allows_back_to_back_items(video: Video) -> None:
    """Half-open bounds: ending exactly where the next begins is not a clash."""
    make_item(video, START, 30)

    adjacent = Scheduleitem(
        video=video,
        schedulereason=Scheduleitem.REASON_ADMIN,
        starttime=START + timedelta(minutes=30),
        duration=timedelta(minutes=10),
    )

    adjacent.full_clean()


def test_allows_editing_an_unrelated_field_on_an_overlapping_row(video: Video) -> None:
    """The guard that keeps the admin usable on historical data.

    The database holds ~1794 overlapping pairs from years of unvalidated
    writes. Fixing a typo on one of them must not fail on a conflict the
    editor neither caused nor can resolve.
    """
    make_item(video, START, 30)
    overlapping = make_item(video, START + timedelta(minutes=10), 10)

    overlapping.default_name = "Renamed, timing untouched"
    overlapping.full_clean()


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
    """Matches range semantics: an empty interval cannot intersect anything."""
    make_item(video, START, 30)

    instant = Scheduleitem(
        video=video,
        schedulereason=Scheduleitem.REASON_ADMIN,
        starttime=START + timedelta(minutes=10),
        duration=timedelta(0),
    )

    instant.full_clean()
