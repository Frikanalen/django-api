"""
The query-parameter filter contracts of the video and videofile lists.

The lookup tables are kept from the fixture era - they are good,
compact statements of the filter API - but the data is now built
in-test, each lookup runs as its own parametrized case (one failing
filter no longer hides the rest), and the videofile lookups reference
real primary keys instead of hardcoded fixture ids.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import FileFormat, Organization, User, Video, VideoFile

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalogue() -> dict[str, Video]:
    """Four videos with pairwise-distinguishable attributes.

    'broken video' has proper_import=False and is therefore invisible
    to the default list queryset.
    """
    nuug_user = User.objects.create(email="nuug_user@fake.com")
    dummy_user = User.objects.create(email="dummy_user@fake.com")
    staff_user = User.objects.create(email="staff_user@fake.com", is_superuser=True)
    # The videos need an organization with an active editor, or the
    # list hides them all; accountability is covered by its own module.
    organization = Organization.objects.create(name="Filter test org", editor=nuug_user)

    tech = Video.objects.create(
        name="tech video",
        organization=organization,
        creator=nuug_user,
        duration=timedelta(seconds=10, milliseconds=10),
        publish_on_web=True,
        proper_import=True,
        ref_url="a",
    )
    dummy = Video.objects.create(
        name="dummy video",
        organization=organization,
        creator=dummy_user,
        duration=timedelta(minutes=1),
        publish_on_web=True,
        proper_import=True,
        ref_url="b",
        framerate=24000,
    )
    unpublished = Video.objects.create(
        name="unpublished video",
        organization=organization,
        creator=staff_user,
        duration=timedelta(milliseconds=1),
        publish_on_web=False,
        proper_import=True,
        has_tono_records=True,
        ref_url="aa",
    )
    broken = Video.objects.create(
        name="broken video",
        organization=organization,
        creator=staff_user,
        duration=timedelta(seconds=1),
        publish_on_web=False,
        proper_import=False,
        ref_url="ab",
    )

    original = FileFormat.objects.create(fsname="original")
    broadcast = FileFormat.objects.create(fsname="broadcast")
    VideoFile.objects.create(video=tech, format=original, filename="tech_video.mp4")
    VideoFile.objects.create(video=dummy, format=original, filename="dummy_video.mov")
    VideoFile.objects.create(video=unpublished, format=broadcast, filename="unpublished_video.dv")
    VideoFile.objects.create(video=broken, format=original, filename="broken_video.mov")

    return {"tech": tech, "dummy": dummy, "unpublished": unpublished, "broken": broken}


VIDEO_LOOKUPS = [
    ("?duration=01:00", ["dummy video"]),
    ("?duration__gte=01:00", ["dummy video"]),
    ("?duration__lt=01:00", ["unpublished video", "tech video"]),
    ("?has_tono_records=false", ["dummy video", "tech video"]),
    ("?has_tono_records=true", ["unpublished video"]),
    ("?framerate=24000", ["dummy video"]),
    ("?framerate=25000", ["unpublished video", "tech video"]),
    ("?name=dummy", []),
    ("?name=dummy+video", ["dummy video"]),
    ("?name__icontains=Dum", ["dummy video"]),
    ("?name__icontains=u", ["unpublished video", "dummy video"]),
    ("?played_count_web__gt=1", []),
    ("?played_count_web__gte=0", ["unpublished video", "dummy video", "tech video"]),
    ("?publish_on_web=false", ["unpublished video"]),
    ("?publish_on_web=true&name__icontains=unpublish", []),
    ("?ref_url=a", ["tech video"]),
    ("?ref_url=b", ["dummy video"]),
    ("?ref_url__startswith=b", ["dummy video"]),
    ("?ref_url__startswith=a", ["unpublished video", "tech video"]),
    ("?creator__email=nuug", []),
    ("?creator__email=nuug_user@fake.com", ["tech video"]),
    ("?creator__email=dummy_user@fake.com&name=", ["dummy video"]),
    # VideoList.get_queryset reads this one straight off the query
    # string in camelCase, unlike the django-filter fields above.
    (
        "?properImport=false",
        ["broken video", "unpublished video", "dummy video", "tech video"],
    ),
    ("?properImport=true", ["unpublished video", "dummy video", "tech video"]),
]


@pytest.mark.parametrize(("lookup", "expected"), VIDEO_LOOKUPS, ids=[q for q, _ in VIDEO_LOOKUPS])
def test_video_list_filters(catalogue, lookup: str, expected: list[str]) -> None:
    response = APIClient().get(reverse("api-video-list") + lookup)

    assert response.status_code == 200, lookup
    assert [video["name"] for video in response.json()["results"]] == expected


VIDEOFILE_LOOKUPS = [
    ("?video_id={tech_pk}", ["tech_video.mp4"]),
    ("?video_id={dummy_pk}", ["dummy_video.mov"]),
    ("?format__fsname=broadcast", ["unpublished_video.dv"]),
]


@pytest.mark.parametrize(
    ("lookup", "expected"), VIDEOFILE_LOOKUPS, ids=[q for q, _ in VIDEOFILE_LOOKUPS]
)
def test_videofile_list_filters(catalogue, lookup: str, expected: list[str]) -> None:
    lookup = lookup.format(tech_pk=catalogue["tech"].pk, dummy_pk=catalogue["dummy"].pk)

    response = APIClient().get(reverse("api-videofile-list") + lookup)

    assert response.status_code == 200, lookup
    assert [item["filename"] for item in response.json()["results"]] == expected
