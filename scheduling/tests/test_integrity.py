"""The database keeps a grant's compiled rows inside its window."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction

from fk.models import User
from scheduling import services
from scheduling.models import AirtimeGrant

from .conftest import NOW
from .test_services import OPEN_WEEK, schedule

pytestmark = pytest.mark.django_db
State = AirtimeGrant.State


def check_now() -> None:
    """Run the deferred checks now instead of at commit, which a test never reaches."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.fixture
def compiled(make_grant, make_video) -> AirtimeGrant:
    grant = make_grant(OPEN_WEEK, state=State.LOCKED)
    services._replace_items(grant, [make_video(20), make_video(10)])
    services.compile_grant(grant, NOW)
    return grant


def test_a_grant_cannot_end_before_what_it_put_on_the_schedule(compiled) -> None:
    with pytest.raises(IntegrityError, match="outside its airtime grant"), transaction.atomic():
        AirtimeGrant.objects.filter(pk=compiled.pk).update(
            endtime=OPEN_WEEK + timedelta(minutes=15)
        )
        check_now()


def test_a_row_cannot_run_past_its_grant(compiled) -> None:
    row = compiled.schedule_items.order_by("starttime").last()
    with pytest.raises(IntegrityError, match="outside its airtime grant"), transaction.atomic():
        row.duration = timedelta(hours=2)
        row.save(update_fields=["duration"])
        check_now()


def test_moving_the_end_through_the_service_keeps_the_rows_inside(compiled) -> None:
    staff = User.objects.create(email="staff@example.test", is_superuser=True)
    at_1910 = OPEN_WEEK + timedelta(minutes=10)
    with transaction.atomic():
        services.update_grant(compiled, endtime=OPEN_WEEK + timedelta(minutes=15), now=at_1910)
        services.set_items(compiled, [], user=staff, now=at_1910)
        check_now()
    assert schedule(compiled)[0][2] == timedelta(minutes=10)
