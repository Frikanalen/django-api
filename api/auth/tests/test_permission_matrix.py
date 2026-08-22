"""
The permission matrix: who may read, create, modify and delete what.

One parametrized grid per resource family replaces the old
PermissionsTest class hierarchy, whose claims were scattered over three
files and whose 'cannot mutate' test only ever sent POSTs. Every denied
row also proves the object was left untouched.

Organization objects have their own equivalent grid in
api/organization/tests/test_permissions.py, and upload-token visibility
is covered in api/video/tests/test_upload_token.py.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import AsRun, Category, Organization, Scheduleitem, Video, VideoFile

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("now_in_the_drafting_week")]

DETAIL_URLS = {
    "video": "api-video-detail",
    "videofile": "api-videofile-detail",
    "scheduleitem": "api-scheduleitem-detail",
    "asrun": "asrun-detail",
    "category": "category-detail",
}

# (payload, field, value stored on success) for a minimal valid PATCH.
PATCHES = {
    "video": ({"name": "Renamed video"}, "name", "Renamed video"),
    "videofile": ({"filename": "renamed.mov"}, "filename", "renamed.mov"),
    "scheduleitem": ({"duration": "00:30:00"}, "duration", timedelta(minutes=30)),
    "asrun": ({"playout": "secondary"}, "playout", "secondary"),
    "category": ({"name": "Renamed category"}, "name", "Renamed category"),
}


def modify_and_delete(client, target, obj, patch_status, delete_status):
    url = reverse(DETAIL_URLS[target], args=[obj.pk])
    payload, field, patched_value = PATCHES[target]
    original_value = getattr(obj, field)

    patch_response = client.patch(url, payload, format="json")
    assert patch_response.status_code == patch_status

    obj.refresh_from_db()
    expected_value = patched_value if patch_status == 200 else original_value
    assert getattr(obj, field) == expected_value

    if patch_status != 200:
        # PUT and DELETE must be rejected the same way; the permission
        # check runs before any payload validation, so an empty body
        # suffices.
        assert client.put(url, {}, format="json").status_code == patch_status

    assert client.delete(url).status_code == delete_status
    assert type(obj).objects.filter(pk=obj.pk).exists() == (delete_status != 204)


@pytest.mark.parametrize("target", ["video", "videofile", "scheduleitem"])
@pytest.mark.parametrize(
    ("role", "patch_status", "delete_status"),
    [
        ("anonymous", 401, 401),
        ("outsider", 403, 403),
        ("member", 200, 204),
        ("staff_user", 200, 204),
    ],
)
def test_organization_owned_objects_are_modifiable_by_members_and_staff(
    target, role, patch_status, delete_status, role_client, request
):
    obj = request.getfixturevalue(target)
    modify_and_delete(role_client(role), target, obj, patch_status, delete_status)


@pytest.mark.parametrize("target", ["asrun", "category"])
@pytest.mark.parametrize(
    ("role", "patch_status", "delete_status"),
    [
        ("anonymous", 401, 401),
        ("outsider", 403, 403),
        ("member", 403, 403),
        ("staff_user", 200, 204),
    ],
)
def test_playout_log_and_categories_are_modifiable_by_staff_only(
    target, role, patch_status, delete_status, role_client, request
):
    obj = request.getfixturevalue(target)
    modify_and_delete(role_client(role), target, obj, patch_status, delete_status)


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("anonymous", 401),
        ("outsider", 403),
        ("member", 403),
        ("staff_user", 201),
    ],
)
def test_only_staff_may_log_asrun_entries(role, expected_status, role_client, video):
    response = role_client(role).post(
        reverse("asrun-list"),
        {"video": video.pk, "played_at": "2015-01-01T11:00:00Z"},
        format="json",
    )

    assert response.status_code == expected_status
    if expected_status == 201:
        entry = AsRun.objects.get()
        assert entry.video == video
        assert entry.playout == "main"
    else:
        assert not AsRun.objects.exists()


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("anonymous", 401),
        ("outsider", 403),
        ("member", 403),
        ("staff_user", 201),
    ],
)
def test_only_staff_may_create_categories(role, expected_status, role_client):
    response = role_client(role).post(
        reverse("category-list"),
        {"id": 99, "name": "Created category"},
        format="json",
    )

    assert response.status_code == expected_status
    assert Category.objects.filter(pk=99).exists() == (expected_status == 201)


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("anonymous", 401),
        ("outsider", 403),
        ("member", 201),
        ("staff_user", 201),
    ],
)
def test_creation_on_organization_owned_endpoints_requires_membership(
    role, expected_status, role_client, organization, video
):
    """
    Replaces the pinned create hole: creating a video in an organization,
    attaching a videofile to a video, or scheduling a video for TX
    requires belonging to the owning organization (or being staff).
    """
    client = role_client(role)
    cases = [
        (
            "api-video-list",
            {"name": "Created video", "organization": organization.pk, "categories": []},
        ),
        (
            "api-videofile-list",
            {"video": video.pk, "variant": "original", "filename": "created.mov"},
        ),
        (
            "api-scheduleitem-list",
            {
                "video": video.pk,
                "starttime": "2015-01-02T12:00:00Z",
                "duration": "00:01:00",
                "schedulereason": 2,
            },
        ),
    ]
    for url_name, payload in cases:
        response = client.post(reverse(url_name), payload, format="json")
        assert response.status_code == expected_status, f"{url_name}: {response.content}"

    created = expected_status == 201
    assert Video.objects.filter(name="Created video").exists() == created
    assert VideoFile.objects.filter(filename="created.mov").exists() == created
    assert Scheduleitem.objects.exists() == created


@pytest.fixture
def foreign_video(staff_user):
    foreign_organization = Organization.objects.create(
        name="Foreign organization", editor=staff_user
    )
    return Video.objects.create(
        name="Foreign video",
        creator=staff_user,
        organization=foreign_organization,
        proper_import=True,
    )


@pytest.mark.parametrize("target", ["videofile", "scheduleitem"])
def test_members_cannot_repoint_their_objects_at_a_foreign_video(
    target, member, foreign_video, client_as, request
):
    """
    The object-level check runs against the object as it was, so without
    a target check an update could attach a member's videofile or
    schedule item to another organization's video.
    """
    obj = request.getfixturevalue(target)
    url = reverse(DETAIL_URLS[target], args=[obj.pk])

    response = client_as(member).patch(url, {"video": foreign_video.pk}, format="json")

    assert response.status_code == 403
    obj.refresh_from_db()
    assert obj.video_id != foreign_video.pk


@pytest.mark.parametrize(
    "url_name",
    [
        "api-video-list",
        "api-videofile-list",
        "api-scheduleitem-list",
        "asrun-list",
        "category-list",
    ],
)
def test_anonymous_users_cannot_create_anything(url_name):
    response = APIClient().post(reverse(url_name), {}, format="json")

    assert response.status_code == 401


def test_api_root_links_every_endpoint_with_expected_anonymous_access(
    video, videofile, scheduleitem, asrun, category, organization
):
    expected = {
        "schema": 200,
        "schema/swagger-ui": 200,
        "schema/redoc": 200,
        "csrf": 200,
        "obtain-token": 405,
        "user": 401,
        "user/login": 405,
        "user/logout": 405,
        "user/register": 405,
        "asrun": 200,
        "categories": 200,
        "organization": 200,
        "scheduleitems": 200,
        "scheduling/policy": 200,
        "series": 200,
        "videofiles": 200,
        "videos": 200,
        "tvanytime": 200,
    }
    client = APIClient()

    root_response = client.get(reverse("api-root"))

    assert root_response.status_code == 200
    assert set(root_response.data.keys()) == set(expected)
    for name, expected_status in expected.items():
        assert client.get(root_response.data[name]).status_code == expected_status, name


def test_authenticated_users_reach_their_profile(member, client_as):
    response = client_as(member).get(reverse("api-user-detail"))

    assert response.status_code == 200
    assert response.json()["email"] == member.email
