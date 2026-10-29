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

from fk.models import Organization, User, Video, VideoFile, VideoFileVariant

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

    original = VideoFileVariant.ORIGINAL
    broadcast = VideoFileVariant.BROADCAST
    VideoFile.objects.create(video=tech, variant=original, filename="tech_video.mp4")
    VideoFile.objects.create(video=dummy, variant=original, filename="dummy_video.mov")
    VideoFile.objects.create(video=unpublished, variant=broadcast, filename="unpublished_video.dv")
    VideoFile.objects.create(video=broken, variant=original, filename="broken_video.mov")

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
    # An ordinary django-filter field like the ones above, so `false`
    # selects the unfinished videos rather than lifting the filter.
    ("?proper_import=false", ["broken video"]),
    ("?proper_import=true", ["unpublished video", "dummy video", "tech video"]),
]


@pytest.mark.parametrize(("lookup", "expected"), VIDEO_LOOKUPS, ids=[q for q, _ in VIDEO_LOOKUPS])
def test_video_list_filters(catalogue, lookup: str, expected: list[str]) -> None:
    response = APIClient().get(reverse("api-video-list") + lookup)

    assert response.status_code == 200, lookup
    assert [video["name"] for video in response.json()["results"]] == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("nametoken", ["title-only nametoken"]),
        ("headertoken", ["title-only nametoken"]),
        ("descriptiontoken", ["title-only nametoken"]),
        ("organizationtoken", ["quoted phrase two words", "title-only nametoken"]),
        ("nametoken headertoken", ["title-only nametoken"]),
        ('"two words"', ["quoted phrase two words"]),
        ('"unterminated', []),
    ],
)
def test_video_list_free_text_searches_video_and_organization_fields(
    query: str, expected: list[str]
) -> None:
    user = User.objects.create(email="search_user@fake.com")
    organization = Organization.objects.create(name="organizationtoken", editor=user)

    Video.objects.create(
        name="title-only nametoken",
        description="headertoken descriptiontoken",
        organization=organization,
        creator=user,
        proper_import=True,
    )
    Video.objects.create(
        name="quoted phrase two words",
        organization=organization,
        creator=user,
        proper_import=True,
    )

    response = APIClient().get(reverse("api-video-list"), {"q": query})

    assert response.status_code == 200
    assert [video["name"] for video in response.json()["results"]] == expected


@pytest.fixture
def two_organizations() -> dict[str, Organization]:
    """Two organizations with distinctive names, one video apiece
    carrying a shared search token, plus a silent extra in the first."""
    user = User.objects.create(email="scoped_search_user@fake.com")
    kringkaster = Organization.objects.create(name="Kringkasterhuset", editor=user)
    nabo = Organization.objects.create(name="Nabokanalen", editor=user)

    Video.objects.create(
        name="vidtoken opptak",
        organization=kringkaster,
        creator=user,
        proper_import=True,
    )
    Video.objects.create(
        name="stille opptak",
        organization=kringkaster,
        creator=user,
        proper_import=True,
    )
    Video.objects.create(
        name="vidtoken hos naboen",
        organization=nabo,
        creator=user,
        proper_import=True,
    )

    return {"kringkaster": kringkaster, "nabo": nabo}


def test_video_list_free_text_honours_the_organization_filter(two_organizations) -> None:
    response = APIClient().get(
        reverse("api-video-list"),
        {"q": "vidtoken", "organization": two_organizations["kringkaster"].pk},
    )

    assert response.status_code == 200
    assert [video["name"] for video in response.json()["results"]] == ["vidtoken opptak"]


def test_video_list_free_text_scoped_to_an_organization_ignores_its_name(
    two_organizations,
) -> None:
    """Naming the organization takes its name out of the search.

    Unscoped, an organization name match returns everything it owns -
    that is the point of indexing the name at all.  Scoped to that same
    organization, the caller has already said whose videos they want, so
    the name match would only return the whole catalogue again.
    """
    kringkaster = two_organizations["kringkaster"]

    unscoped = APIClient().get(reverse("api-video-list"), {"q": "Kringkasterhuset"})
    scoped = APIClient().get(
        reverse("api-video-list"), {"q": "Kringkasterhuset", "organization": kringkaster.pk}
    )

    assert unscoped.status_code == 200
    assert [video["name"] for video in unscoped.json()["results"]] == [
        "stille opptak",
        "vidtoken opptak",
    ]
    assert scoped.status_code == 200
    assert [video["name"] for video in scoped.json()["results"]] == []


def test_video_list_free_text_ranks_title_matches_above_description_matches() -> None:
    user = User.objects.create(email="ranking_user@fake.com")
    organization = Organization.objects.create(name="Ranking test org", editor=user)
    Video.objects.create(
        name="rankingtoken title match",
        organization=organization,
        creator=user,
        proper_import=True,
    )
    Video.objects.create(
        name="Description match",
        description="rankingtoken",
        organization=organization,
        creator=user,
        proper_import=True,
    )

    response = APIClient().get(reverse("api-video-list"), {"q": "rankingtoken"})

    assert response.status_code == 200
    assert [video["name"] for video in response.json()["results"]] == [
        "rankingtoken title match",
        "Description match",
    ]


VIDEOFILE_LOOKUPS = [
    ("?video_id={tech_pk}", ["tech_video.mp4"]),
    ("?video_id={dummy_pk}", ["dummy_video.mov"]),
    ("?variant=broadcast", ["unpublished_video.dv"]),
]


@pytest.mark.parametrize(
    ("lookup", "expected"), VIDEOFILE_LOOKUPS, ids=[q for q, _ in VIDEOFILE_LOOKUPS]
)
def test_videofile_list_filters(catalogue, lookup: str, expected: list[str]) -> None:
    lookup = lookup.format(tech_pk=catalogue["tech"].pk, dummy_pk=catalogue["dummy"].pk)

    response = APIClient().get(reverse("api-videofile-list") + lookup)

    assert response.status_code == 200, lookup
    assert [item["filename"] for item in response.json()["results"]] == expected


def test_videofile_variant_filter_rejects_an_unlisted_name(catalogue) -> None:
    """The filter takes its choices from the enum, so a name outside it
    is a bad request rather than a silently empty result."""
    response = APIClient().get(reverse("api-videofile-list") + "?variant=hls")

    assert response.status_code == 400
