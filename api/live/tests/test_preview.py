import time
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core import signing
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import LiveSource, LiveTransmission, Organization, Scheduleitem, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def member() -> User:
    return User.objects.create(email="member@example.test")


@pytest.fixture
def outsider() -> User:
    return User.objects.create(email="outsider@example.test")


@pytest.fixture
def source(member) -> LiveSource:
    organization = Organization.objects.create(name="Contributor")
    organization.members.add(member)
    return LiveSource.objects.create(name="Studio", organization=organization)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def issue_token(user, source):
    return client_for(user).post(reverse("api-live-source-preview-token", args=[source.pk]))


def mediamtx(token, **overrides):
    payload = {
        "user": "",
        "password": "",
        "token": token,
        "ip": "192.0.2.1",
        "action": "read",
        "path": "",
        "protocol": "webrtc",
        "id": str(uuid4()),
        "query": "",
        **overrides,
    }
    return APIClient(enforce_csrf_checks=True).post(
        reverse("api-mediamtx-auth"), payload, format="json"
    )


def book(source, start, duration=timedelta(hours=1), name="Valgvake"):
    item = Scheduleitem.objects.create(
        starttime=start, duration=duration, default_name=name, schedulereason=2, is_live=True
    )
    LiveTransmission.objects.create(schedule_item=item, source=source)
    return item


def test_member_lists_sources_with_only_unfinished_bookings(member, source):
    now = timezone.now()
    book(source, now - timedelta(hours=3), name="Over")
    on_air = book(source, now - timedelta(minutes=30), name="On air")
    later = book(source, now + timedelta(days=2), name="Later")

    response = client_for(member).get(
        reverse("api-organization-live-source-list", args=[source.organization_id])
    )

    assert response.status_code == 200
    [listed] = response.json()
    assert listed["streamId"] == str(source.stream_id)
    assert listed["previewPath"] == f"contrib/preview/{source.stream_id}"
    assert [item["id"] for item in listed["upcomingTransmissions"]] == [on_air.pk, later.pk]
    assert listed["upcomingTransmissions"][1]["name"] == "Later"


def test_outsiders_cannot_list_or_preview(outsider, source):
    listing = client_for(outsider).get(
        reverse("api-organization-live-source-list", args=[source.organization_id])
    )
    assert listing.status_code == 403
    assert issue_token(outsider, source).status_code == 404
    anonymous = APIClient().post(reverse("api-live-source-preview-token", args=[source.pk]))
    assert anonymous.status_code in (401, 403)


@pytest.mark.parametrize("path", ["contrib/preview/{}", "contrib/live/{}"])
def test_token_reads_either_path_of_its_source_over_webrtc(member, source, path):
    response = issue_token(member, source)
    assert response.status_code == 201
    data = response.json()
    assert data["previewPath"] == f"contrib/preview/{source.stream_id}"
    assert mediamtx(data["token"], path=path.format(source.stream_id)).status_code == 204


def test_token_grants_nothing_else(member, source):
    token = issue_token(member, source).json()["token"]
    other = LiveSource.objects.create(name="Other", organization=source.organization)
    path = source.preview_path
    assert mediamtx(token, path=other.preview_path).status_code == 401
    assert mediamtx(token, path=path, action="publish").status_code == 401
    assert mediamtx(token, path=path, protocol="rtsp").status_code == 401
    assert mediamtx(token[:-2] + "xx", path=path).status_code == 401
    assert mediamtx("not-a-token", path=path).status_code == 401


def test_token_expires(member, source, monkeypatch):
    token = issue_token(member, source).json()["token"]
    later = time.time() + 6 * 60
    monkeypatch.setattr(signing.time, "time", lambda: later)
    assert mediamtx(token, path=source.preview_path).status_code == 401


def test_access_is_rechecked_when_the_token_is_used(member, source):
    token = issue_token(member, source).json()["token"]
    source.organization.members.remove(member)
    assert mediamtx(token, path=source.preview_path).status_code == 401


def test_disabled_source_and_inactive_user_are_refused(member, source):
    token = issue_token(member, source).json()["token"]
    source.enabled = False
    source.save()
    assert mediamtx(token, path=source.preview_path).status_code == 401
    assert issue_token(member, source).status_code == 404

    source.enabled = True
    source.save()
    member.is_active = False
    member.save()
    assert mediamtx(token, path=source.preview_path).status_code == 401
