from datetime import date, datetime, timezone

import pytest

from fk.models import Scheduleitem


pytestmark = pytest.mark.django_db


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
