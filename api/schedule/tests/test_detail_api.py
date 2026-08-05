from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Scheduleitem

pytestmark = pytest.mark.django_db

HISTORICAL_START = datetime(2015, 1, 1, 10, tzinfo=ZoneInfo("Europe/Oslo"))


def test_retrieve_schedule_item_ignores_list_date_window(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    item = schedule_item_factory(starttime=HISTORICAL_START)

    response = authenticated_client.get(
        reverse("api-scheduleitem-detail", args=[item.pk])
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["id"] == item.pk


def test_delete_schedule_item_ignores_list_date_window(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    item = schedule_item_factory(starttime=HISTORICAL_START)

    response = authenticated_client.delete(
        reverse("api-scheduleitem-detail", args=[item.pk])
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Scheduleitem.objects.filter(pk=item.pk).exists()
