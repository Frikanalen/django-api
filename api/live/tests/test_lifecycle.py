"""How a live source and its encoder keys begin, change and end."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import (
    LiveSource,
    LiveTransmission,
    Organization,
    Scheduleitem,
    StreamCredential,
    User,
    Video,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def organization() -> Organization:
    return Organization.objects.create(name="Contributor", fkmember=True)


@pytest.fixture
def member(organization) -> User:
    user = User.objects.create(email="member@example.test", identity_confirmed=True)
    organization.members.add(user)
    return user


@pytest.fixture
def source(organization) -> LiveSource:
    return LiveSource.objects.create(name="Studio", organization=organization)


@pytest.fixture
def client(member):
    client = APIClient()
    client.force_authenticate(user=member)
    return client


def detail(source):
    return reverse("api-live-source-detail", args=[source.pk])


def keys(source):
    return reverse("api-live-source-encoder-keys", args=[source.pk])


def programme(source, name="Valgvake") -> Video:
    """A video fed by `source`, not yet in the schedule."""
    video = Video.objects.create(
        name=name,
        organization=source.organization,
        creator=User.objects.get_or_create(email="booker@example.test")[0],
        duration=timedelta(hours=1),
    )
    LiveTransmission.objects.create(video=video, source=source)
    return video


def book(source, start, name="Valgvake") -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=programme(source, name),
        starttime=start,
        duration=timedelta(hours=1),
        schedulereason=2,
        is_live=True,
    )


def mediamtx(key_id, password, source):
    return APIClient().post(
        reverse("api-mediamtx-auth"),
        {
            "user": str(key_id),
            "password": password,
            "token": "",
            "ip": "192.0.2.1",
            "action": "publish",
            "path": source.contribution_path,
            "protocol": "srt",
            "id": str(uuid4()),
            "query": "",
        },
        format="json",
    )


def test_a_source_says_how_many_encoders_hold_a_key(client, source):
    assert client.get(detail(source)).data["encoder_count"] == 0

    first = client.post(keys(source), {"label": "ATEM"}, format="json").data
    client.post(keys(source), {"label": "OBS"}, format="json")
    assert client.get(detail(source)).data["encoder_count"] == 2

    client.delete(reverse("api-live-source-encoder-key-revoke", args=[source.pk, first["id"]]))
    listed = client.get(
        reverse("api-organization-live-source-list", args=[source.organization_id])
    ).data
    assert [entry["encoder_count"] for entry in listed] == [1]


def test_a_source_never_used_is_deleted_with_its_keys(client, source):
    client.post(keys(source), {"label": "ATEM"}, format="json")
    assert client.get(detail(source)).data["can_delete"] is True

    assert client.delete(detail(source)).status_code == 204

    assert not LiveSource.objects.filter(pk=source.pk).exists()
    assert not StreamCredential.objects.filter(source_id=source.pk).exists()


def test_a_source_a_programme_has_used_stays(client, source):
    book(source, timezone.now() - timedelta(days=3))
    assert client.get(detail(source)).data["can_delete"] is False

    response = client.delete(detail(source))

    assert response.status_code == 400
    assert "Slå den av" in response.content.decode()
    assert LiveSource.objects.filter(pk=source.pk).exists()
    # Its airing is over, so it can be switched off.
    assert client.patch(detail(source), {"enabled": False}, format="json").status_code == 200


def test_only_those_who_may_set_up_delete(source, organization):
    unconfirmed = User.objects.create(email="new@example.test")
    organization.members.add(unconfirmed)
    client = APIClient()
    client.force_authenticate(user=unconfirmed)
    assert client.delete(detail(source)).status_code == 403
    outsider = APIClient()
    outsider.force_authenticate(user=User.objects.create(email="out@example.test"))
    assert outsider.delete(detail(source)).status_code == 404
    assert LiveSource.objects.filter(pk=source.pk).exists()


@pytest.mark.parametrize("starts_in", [timedelta(hours=5), -timedelta(minutes=20)])
def test_a_source_with_an_airing_coming_or_on_cannot_be_switched_off(client, source, starts_in):
    book(source, timezone.now() + starts_in, name="Høstkonsert")

    response = client.patch(detail(source), {"enabled": False}, format="json")

    assert response.status_code == 400
    assert "Høstkonsert" in response.content.decode()
    source.refresh_from_db()
    assert source.enabled
    # Renaming is no threat to the airing.
    assert client.patch(detail(source), {"name": "Hall"}, format="json").status_code == 200


def test_how_the_signal_comes_in_cannot_change_under_an_airing(client, source, monkeypatch):
    from fk import pull_sources

    monkeypatch.setattr(pull_sources, "is_configured", lambda: True)
    monkeypatch.setattr(pull_sources, "validate", lambda url: None)
    monkeypatch.setattr(pull_sources, "encrypt", lambda url: "sealed")
    key = client.post(keys(source), {"label": "ATEM"}, format="json").data
    book(source, timezone.now() + timedelta(hours=5))
    address = {"pull_url": "srt://encoder.example.test:9000"}

    assert client.patch(detail(source), address, format="json").status_code == 400
    assert StreamCredential.objects.get(pk=key["id"]).revoked_at is None

    Scheduleitem.objects.all().delete()
    assert client.patch(detail(source), address, format="json").status_code == 200
    # A pulled source may get a new address even when booked: it stays pulled.
    book(source, timezone.now() + timedelta(hours=5), name="Later")
    other = {"pull_url": "srt://other.example.test:9000"}
    assert client.patch(detail(source), other, format="json").status_code == 200
    assert client.patch(detail(source), {"pull_url": ""}, format="json").status_code == 400


def test_replacing_a_key_keeps_the_encoder_and_retires_the_old_password(client, source):
    old = client.post(keys(source), {"label": "ATEM i studio"}, format="json").data
    assert mediamtx(old["key_id"], old["password"], source).status_code == 204

    response = client.post(
        reverse("api-live-source-encoder-key-replace", args=[source.pk, old["id"]])
    )

    assert response.status_code == 201, response.content
    new = response.data
    assert new["label"] == "ATEM i studio"
    assert new["id"] != old["id"]
    assert new["password"] != old["password"]
    assert "connections" in new and "blackmagic_config" in new
    assert mediamtx(old["key_id"], old["password"], source).status_code == 401
    assert mediamtx(new["key_id"], new["password"], source).status_code == 204
    assert [k["id"] for k in client.get(keys(source)).data] == [new["id"]]
    # A retired key is not replaced twice.
    again = reverse("api-live-source-encoder-key-replace", args=[source.pk, old["id"]])
    assert client.post(again).status_code == 404


def test_a_key_remembers_when_it_was_last_used(client, source):
    key = client.post(keys(source), {"label": "ATEM"}, format="json").data
    assert client.get(keys(source)).data[0]["last_used_at"] is None

    assert mediamtx(key["key_id"], "wrong", source).status_code == 401
    assert client.get(keys(source)).data[0]["last_used_at"] is None

    assert mediamtx(key["key_id"], key["password"], source).status_code == 204
    used = StreamCredential.objects.get(pk=key["id"]).last_used_at
    assert used is not None and timezone.now() - used < timedelta(seconds=10)

    # Kept to the minute: an encoder that reconnects at once costs no write.
    assert mediamtx(key["key_id"], key["password"], source).status_code == 204
    assert StreamCredential.objects.get(pk=key["id"]).last_used_at == used
