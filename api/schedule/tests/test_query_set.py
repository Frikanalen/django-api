from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone as django_timezone

from fk.models import Scheduleitem


pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            datetime(2015, 1, 1, 23, 30, tzinfo=timezone.utc),
            date(2015, 1, 2),
            id="timezone-aware-datetime",
        ),
        pytest.param("2015-01-02", date(2015, 1, 2), id="iso-date"),
        pytest.param("not-a-date", None, id="invalid-date"),
        pytest.param(42, None, id="unsupported-type"),
    ],
)
def test_normalize_date(value: object, expected: date | None) -> None:
    assert Scheduleitem.objects.normalize_date(value) == expected


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
