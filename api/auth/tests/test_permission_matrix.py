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

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import AsRun, Category, Video, VideoFile

pytestmark = pytest.mark.django_db

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
    "scheduleitem": ({"schedulereason": 2}, "schedulereason", 2),
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


def test_creation_on_organization_owned_endpoints_is_open_to_any_authenticated_user(
    outsider, organization, video, file_format, client_as
):
    """
    Known hole, pinned on purpose: the IsInOrganization* permissions only
    guard *object-level* access, and DRF never runs object checks on
    create. Any authenticated user - regardless of membership - can
    therefore create a video in someone else's organization, attach a
    videofile to someone else's video, and insert items into the TX
    schedule. Closing the hole is a behavior change that should replace
    this test; until then it documents what the API actually allows.
    """
    client = client_as(outsider)
    cases = [
        (
            "api-video-list",
            {"name": "Hole video", "organization": organization.pk, "categories": []},
        ),
        (
            "api-videofile-list",
            {"video": video.pk, "format": file_format.pk, "filename": "hole.mov"},
        ),
        (
            "api-scheduleitem-list",
            {
                "video": video.pk,
                "starttime": "2015-06-01T12:00:00Z",
                "duration": "00:01:00",
                "schedulereason": 2,
            },
        ),
    ]
    for url_name, payload in cases:
        response = client.post(reverse(url_name), payload, format="json")
        assert response.status_code == 201, f"{url_name}: {response.content}"

    assert Video.objects.filter(name="Hole video").exists()
    assert VideoFile.objects.filter(filename="hole.mov").exists()


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
        "asrun": 200,
        "category": 200,
        "jukebox-csv": 200,
        "obtain-token": 405,
        "scheduleitems": 200,
        "videofiles": 200,
        "videos": 200,
        "organization": 200,
        "user": 401,
        "user/register": 405,
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
