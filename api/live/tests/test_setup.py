"""Members of a member organization set up their own live transmission."""

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import LiveSource, Organization, StreamCredential, User

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


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def create_source(user, organization, name="Studio 2"):
    return client_for(user).post(
        reverse("api-organization-live-source-list", args=[organization.pk]),
        {"name": name},
        format="json",
    )


def issue_key(user, source, label="ATEM"):
    return client_for(user).post(
        reverse("api-live-source-encoder-keys", args=[source.pk]), {"label": label}, format="json"
    )


def test_a_member_creates_renames_and_disables_a_source(member, organization):
    response = create_source(member, organization)
    assert response.status_code == 201, response.content
    created = LiveSource.objects.get(pk=response.data["id"])
    assert (created.organization, created.name, created.enabled) == (organization, "Studio 2", True)
    assert response.data["contribution_path"] == f"contrib/live/{created.stream_id}"

    detail = reverse("api-live-source-detail", args=[created.pk])
    response = client_for(member).patch(detail, {"name": "Hall", "enabled": False}, format="json")
    assert response.status_code == 200, response.content
    created.refresh_from_db()
    assert (created.name, created.enabled) == ("Hall", False)


def test_the_stream_id_and_organization_are_not_the_members_to_choose(member, organization):
    other = Organization.objects.create(name="Other", fkmember=True)
    response = client_for(member).post(
        reverse("api-organization-live-source-list", args=[organization.pk]),
        {
            "name": "Studio",
            "stream_id": "00000000-0000-0000-0000-000000000000",
            "organization": other.pk,
        },
        format="json",
    )
    assert response.status_code == 201
    created = LiveSource.objects.get(pk=response.data["id"])
    assert created.organization == organization
    assert str(created.stream_id) != "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    "who",
    ["unconfirmed", "non_member_org"],
)
def test_only_confirmed_members_of_member_organizations_set_up(who, member, organization, source):
    if who == "unconfirmed":
        member.identity_confirmed = False
        member.save()
    else:
        organization.fkmember = False
        organization.save()

    assert create_source(member, organization).status_code == 403
    assert issue_key(member, source).status_code == 403
    response = client_for(member).patch(
        reverse("api-live-source-detail", args=[source.pk]), {"name": "x"}, format="json"
    )
    assert response.status_code == 403
    # They can still look at what staff set up for them.
    listed = client_for(member).get(
        reverse("api-organization-live-source-list", args=[organization.pk])
    )
    assert [s["name"] for s in listed.data] == ["Studio"]


def test_outsiders_see_nothing(source, organization):
    outsider = User.objects.create(email="outsider@example.test", identity_confirmed=True)
    Organization.objects.create(name="Theirs", fkmember=True).members.add(outsider)

    assert create_source(outsider, organization).status_code == 403
    assert issue_key(outsider, source).status_code == 404
    client = client_for(outsider)
    assert client.get(reverse("api-live-source-detail", args=[source.pk])).status_code == 404
    keys = reverse("api-live-source-encoder-keys", args=[source.pk])
    assert client.get(keys).status_code == 404


@override_settings(
    FK_MEDIAMTX_SRT_URL="srt://live.example.test:8890",
    FK_MEDIAMTX_RTMP_URL="rtmp://live.example.test:1935",
)
def test_a_key_is_shown_once_with_its_encoder_settings(member, source):
    response = issue_key(member, source)
    assert response.status_code == 201, response.content
    assert response["Cache-Control"].startswith("max-age=0")
    password = response.data["password"]
    credential = StreamCredential.objects.get(pk=response.data["id"])
    assert credential.action == StreamCredential.Action.PUBLISH
    assert credential.source == source
    assert credential.check_secret(password)
    labels = [c["label"] for c in response.data["connections"]]
    assert labels == ["SRT", "RTMP"]
    assert password in response.data["connections"][1]["url"]
    assert "<streaming>" in response.data["blackmagic_config"]

    listed = client_for(member).get(reverse("api-live-source-encoder-keys", args=[source.pk]))
    assert listed.status_code == 200
    assert [k["label"] for k in listed.data] == ["ATEM"]
    assert "password" not in listed.data[0]
    assert password not in listed.content.decode()


def test_members_see_and_revoke_only_encoder_keys(member, source):
    reader = StreamCredential(source=source, label="Staff reader", action="read")
    reader.generate_secret()
    reader.save()
    key_id = issue_key(member, source).data["id"]

    keys = reverse("api-live-source-encoder-keys", args=[source.pk])
    assert [k["id"] for k in client_for(member).get(keys).data] == [key_id]

    client = client_for(member)
    revoke = reverse("api-live-source-encoder-key-revoke", args=[source.pk, reader.pk])
    assert client.delete(revoke).status_code == 404
    revoke = reverse("api-live-source-encoder-key-revoke", args=[source.pk, key_id])
    assert client.delete(revoke).status_code == 204
    assert StreamCredential.objects.get(pk=key_id).revoked_at is not None
    assert client.get(keys).data == []


def test_a_disabled_source_issues_no_keys(member, source):
    source.enabled = False
    source.save()
    assert issue_key(member, source).status_code == 403
