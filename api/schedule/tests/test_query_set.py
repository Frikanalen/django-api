from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone as django_timezone

from fk.models import Scheduleitem, airtime_end

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            datetime(2015, 1, 1, 23, 30, tzinfo=UTC),
            date(2015, 1, 2),
            id="timezone-aware-datetime",
        ),
        pytest.param("2015-01-02", date(2015, 1, 2), id="iso-date"),
        pytest.param("not-a-date", None, id="invalid-date"),
        pytest.param(42, None, id="unsupported-type"),
    ],
)
def test_normalize_date(value: object, expected: date | None) -> None:
    # The 42 case is deliberately outside the declared parameter type:
    # normalize_date's final `return None` exists to absorb whatever a
    # query parameter hands it, and this pins that it does.
    assert Scheduleitem.objects.normalize_date(value) == expected  # type: ignore[arg-type]


def test_by_day_without_a_start_date_anchors_on_the_current_oslo_day(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    """`xmltv_upcoming` calls `by_day(days=7)`, relying on the implicit start date."""
    today = django_timezone.localdate()
    schedule_item_factory(
        starttime=datetime.combine(today - timedelta(days=1), time(23, 59), tzinfo=OSLO)
    )
    expected = schedule_item_factory(starttime=datetime.combine(today, time(0, 1), tzinfo=OSLO))

    assert list(Scheduleitem.objects.by_day(days=1)) == [expected]


def test_overlapping_excludes_back_to_back_items(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    """The bounds are half-open, so an item ending exactly when the window
    opens is not a conflict. Pinned here because the check moved from a
    pair of scalar comparisons to a range `&&`."""
    schedule_item_factory(
        starttime=datetime(2015, 1, 1, 10, tzinfo=OSLO), duration=timedelta(hours=1)
    )

    abutting = Scheduleitem.objects.overlapping(
        datetime(2015, 1, 1, 11, tzinfo=OSLO), datetime(2015, 1, 1, 12, tzinfo=OSLO)
    )
    straddling = Scheduleitem.objects.overlapping(
        datetime(2015, 1, 1, 10, 30, tzinfo=OSLO), datetime(2015, 1, 1, 11, 30, tzinfo=OSLO)
    )

    assert list(abutting) == []
    assert len(straddling) == 1


def test_airtime_counts_elapsed_time_across_the_autumn_transition(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    """Oslo repeats 02:00-03:00 on 2026-10-25.

    An hour of airtime is an hour of real time -- playout does not pause for
    the clock going back -- so an item starting 02:30 CEST is off the air at
    01:30 UTC. Python's `starttime + duration` is wall-clock arithmetic and
    would put it an hour later, which is why `airtime_end` exists.
    """
    start = datetime(2026, 10, 25, 2, 30, tzinfo=OSLO)
    duration = timedelta(hours=1)
    item = schedule_item_factory(starttime=start, duration=duration)
    item.refresh_from_db()

    assert item.airtime.upper == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert airtime_end(start, duration) == item.airtime.upper
    # The arithmetic the validation paths used to do, kept here to show what
    # the helper is guarding against: an hour of wall clock, two of airtime.
    assert (start + duration).astimezone(UTC) == datetime(2026, 10, 25, 2, 30, tzinfo=UTC)


def test_a_zero_length_item_ends_where_it_starts(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    """A zero-length item generates an *empty* range, whose bounds are both
    None rather than the starttime. Reading `airtime.upper` straight would
    report no end time at all -- null over the API, an empty `stop` in the
    XMLTV feed, and a TypeError in the jukebox's gap search.
    """
    item = schedule_item_factory(
        starttime=datetime(2015, 1, 1, 10, tzinfo=OSLO), duration=timedelta(0)
    )
    item.refresh_from_db()

    assert item.airtime.upper is None
    assert item.endtime == item.starttime


def test_a_zero_length_item_occupies_no_airtime(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    """It cannot collide with anything, which is the rule Scheduleitem.clean()
    has always stated. An empty range overlaps nothing, so the column enforces
    it rather than each caller having to remember."""
    schedule_item_factory(starttime=datetime(2015, 1, 1, 10, tzinfo=OSLO), duration=timedelta(0))

    around_it = Scheduleitem.objects.overlapping(
        datetime(2015, 1, 1, 9, tzinfo=OSLO), datetime(2015, 1, 1, 11, tzinfo=OSLO)
    )

    assert list(around_it) == []


def test_airtime_follows_an_item_that_is_moved(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    """Django reads a generated column back on INSERT but not on UPDATE, so
    without help the moved item still reports where it used to air -- and that
    is the value the API hands back to whoever just moved it."""
    item = schedule_item_factory(
        starttime=datetime(2015, 1, 1, 10, tzinfo=OSLO), duration=timedelta(hours=1)
    )

    item.starttime = datetime(2015, 1, 1, 14, tzinfo=OSLO)
    item.save()

    assert item.endtime == datetime(2015, 1, 1, 15, tzinfo=OSLO)
    assert item.airtime.lower == datetime(2015, 1, 1, 14, tzinfo=OSLO)
