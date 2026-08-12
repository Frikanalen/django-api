"""
Who may edit a video through the members' pages.

The rule lives in agenda.views.allowed_to_edit and guards both the GET
and the POST of ManageVideoEdit, so each case is checked against both:
a rule that only holds on the form-rendering half would leave the write
open.
"""

import pytest
from django.test import Client
from django.urls import reverse

from fk.models import Category, Organization, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def organization() -> Organization:
    return Organization.objects.create(name="Owning org")


@pytest.fixture
def member(organization: Organization) -> User:
    user = User.objects.create(email="member@example.test")
    organization.members.add(user)
    return user


@pytest.fixture
def category() -> Category:
    # The form requires at least one: Video.categories is a plain
    # ManyToManyField, so the field is not blank-able.
    return Category.objects.create(id=1, name="Kultur")


@pytest.fixture
def video(organization: Organization, member: User, category: Category) -> Video:
    video = Video.objects.create(name="Their video", creator=member, organization=organization)
    video.categories.add(category)
    return video


def client_for(user: User | None) -> Client:
    client = Client()
    if user is not None:
        client.force_login(user)
    return client


def edit_url(video: Video) -> str:
    return reverse("manage-video-edit", args=(video.id,))


def post_data(video: Video, **overrides) -> dict:
    """A payload the form accepts, so a 200 means the edit went through
    rather than the form bouncing it back."""
    data = {
        "name": video.name,
        "organization": video.organization_id,
        "duration": "00:00:00",
        "categories": list(video.categories.values_list("id", flat=True)),
        "header": "",
        "ref_url": "",
    }
    data.update(overrides)
    return data


def test_anonymous_visitors_are_sent_to_the_login_page(video: Video) -> None:
    response = client_for(None).get(edit_url(video))

    assert response.status_code == 302
    assert response["Location"].startswith("/login/?next=")


def test_a_member_of_the_owning_organization_may_edit(member: User, video: Video) -> None:
    client = client_for(member)

    assert client.get(edit_url(video)).status_code == 200

    client.post(edit_url(video), post_data(video, name="Renamed by a member"))
    video.refresh_from_db()
    assert video.name == "Renamed by a member"


def test_an_unrelated_user_is_refused(video: Video) -> None:
    stranger = User.objects.create(email="stranger@example.test")
    client = client_for(stranger)

    assert client.get(edit_url(video)).status_code == 403

    client.post(edit_url(video), post_data(video, name="Renamed by a stranger"))
    video.refresh_from_db()
    assert video.name == "Their video"


def test_a_member_of_another_organization_is_refused(video: Video) -> None:
    """Membership is of one organization, not of the site."""
    other_org = Organization.objects.create(name="Some other org")
    outsider = User.objects.create(email="outsider@example.test")
    other_org.members.add(outsider)

    assert client_for(outsider).get(edit_url(video)).status_code == 403


def test_staff_may_edit_anything(video: Video) -> None:
    admin = User.objects.create(email="admin@example.test", is_superuser=True)

    assert client_for(admin).get(edit_url(video)).status_code == 200


def test_the_organizations_editor_is_not_admitted(organization: Organization, video: Video) -> None:
    """A known divergence, pinned rather than endorsed.

    The API's can_administer_organization() admits the ansvarlig
    redaktor alongside the members; these pages admit only the members,
    so an editor who never joined their own organization as a member is
    refused here and allowed there. Nothing chose that -- the two checks
    were written apart. Change this test when the difference is decided
    either way.
    """
    editor = User.objects.create(email="editor@example.test")
    organization.editor = editor
    organization.save()

    assert client_for(editor).get(edit_url(video)).status_code == 403
