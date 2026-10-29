from datetime import time, timedelta

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import (
    Organization,
    SlotSourceStrategy,
    SlotSourceType,
    User,
    Video,
    WeeklySlot,
    WeeklySlotSource,
)

pytestmark = pytest.mark.django_db


def test_member_can_create_and_modify_an_organization_source(
    member_client: APIClient,
    organization: Organization,
    video: Video,
) -> None:
    response = member_client.post(
        reverse("api-weekly-slot-source-list"),
        {
            "name": "Selected programmes",
            "type": SlotSourceType.VIDEOS,
            "strategy": SlotSourceStrategy.RANDOM,
            "organization": organization.pk,
            "directVideos": [video.pk],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    source = WeeklySlotSource.objects.get(pk=response.json()["id"])
    assert list(source.direct_videos.all()) == [video]

    response = member_client.patch(
        reverse("api-weekly-slot-source-detail", args=[source.pk]),
        {"name": "Least repeated", "strategy": SlotSourceStrategy.LEAST_SCHEDULED},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    source.refresh_from_db()
    assert source.name == "Least repeated"
    assert source.strategy == SlotSourceStrategy.LEAST_SCHEDULED


def test_outsider_cannot_see_or_modify_an_organizations_source(
    source: WeeklySlotSource,
) -> None:
    outsider = User.objects.create(email="weekly-slot-outsider@example.test")
    client = APIClient()
    client.force_authenticate(outsider)

    response = client.patch(
        reverse("api-weekly-slot-source-detail", args=[source.pk]),
        {"name": "Taken over"},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    source.refresh_from_db()
    assert source.name == "Organization uploads"


def test_source_rejects_a_video_from_another_organization(
    member: User,
    member_client: APIClient,
    organization: Organization,
) -> None:
    other = Organization.objects.create(name="Other organization", editor=member)
    foreign_video = Video.objects.create(
        name="Someone else's programme",
        creator=member,
        organization=other,
    )

    response = member_client.post(
        reverse("api-weekly-slot-source-list"),
        {
            "name": "Mixed ownership",
            "type": SlotSourceType.VIDEOS,
            "strategy": SlotSourceStrategy.LATEST,
            "organization": organization.pk,
            "directVideos": [foreign_video.pk],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not WeeklySlotSource.objects.filter(name="Mixed ownership").exists()


def test_member_can_change_the_source_selected_for_their_slot(
    member_client: APIClient,
    organization: Organization,
    slot: WeeklySlot,
) -> None:
    replacement = WeeklySlotSource.objects.create(
        name="Replacement",
        type=SlotSourceType.ORGANIZATION,
        strategy=SlotSourceStrategy.RANDOM,
        organization=organization,
    )

    response = member_client.patch(
        reverse("api-weekly-slot-detail", args=[slot.pk]),
        {"source": replacement.pk},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    slot.refresh_from_db()
    assert slot.source == replacement


def test_member_cannot_move_or_resize_their_slot(
    member_client: APIClient,
    slot: WeeklySlot,
) -> None:
    response = member_client.patch(
        reverse("api-weekly-slot-detail", args=[slot.pk]),
        {"day": 4, "startTime": "21:00", "duration": "02:00:00"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    slot.refresh_from_db()
    assert slot.day == 2
    assert slot.start_time == time(18, 0)
    assert slot.duration == timedelta(hours=1)


def test_member_cannot_select_another_organizations_source(
    member: User,
    member_client: APIClient,
    slot: WeeklySlot,
) -> None:
    other = Organization.objects.create(name="Other source owner", editor=member)
    foreign_source = WeeklySlotSource.objects.create(
        name="Foreign source",
        type=SlotSourceType.ORGANIZATION,
        strategy=SlotSourceStrategy.LATEST,
        organization=other,
    )

    response = member_client.patch(
        reverse("api-weekly-slot-detail", args=[slot.pk]),
        {"source": foreign_source.pk},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    slot.refresh_from_db()
    assert slot.source != foreign_source


def test_unowned_legacy_slot_is_staff_only(
    member_client: APIClient,
    staff_client: APIClient,
    source: WeeklySlotSource,
) -> None:
    slot = WeeklySlot.objects.create(
        day=0,
        start_time="12:00",
        duration=timedelta(hours=1),
    )
    url = reverse("api-weekly-slot-detail", args=[slot.pk])

    assert member_client.patch(url, {"source": source.pk}, format="json").status_code == 403
    assert staff_client.patch(url, {"source": None}, format="json").status_code == 200
