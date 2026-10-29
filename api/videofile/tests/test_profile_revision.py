"""
Which encoding profile produced a file, and finding the files a newer one
has superseded.

This is what lets ingest tell "this video has DASH" apart from "this
video has *current* DASH" without crawling the archive, so the filter
below is the whole of its backfill planning query.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import Organization, User, Video, VideoFile, VideoFileVariant

pytestmark = pytest.mark.django_db


@pytest.fixture
def ingest_client() -> APIClient:
    service = User.objects.create(email="profile-ingest@example.test", is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=service)
    return client


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="profile-editor@example.test")
    organization = Organization.objects.create(name="Profile org", editor=editor)
    organization.members.add(editor)
    return Video.objects.create(
        name="Profile revision video",
        creator=editor,
        organization=organization,
        proper_import=True,
    )


def make_file(video: Video, variant: VideoFileVariant, **fields) -> VideoFile:
    return VideoFile.objects.create(
        video=video,
        variant=variant,
        filename=f"{variant}.bin",
        **fields,
    )


def filenames(response) -> set[str]:
    return {item["filename"] for item in response.json()["results"]}


def test_a_file_registered_without_a_revision_predates_tracking(video: Video) -> None:
    """Zero, not null. Ingest numbers its profile templates from 1, so no
    real profile can claim the value -- and it is the truth about every
    row that was in the table before the column existed."""
    assert make_file(video, VideoFileVariant.DASH).profile_revision == 0


def test_ingest_stamps_the_revision_when_it_registers_a_file(
    ingest_client: APIClient, video: Video
) -> None:
    response = ingest_client.post(
        reverse("api-videofile-list"),
        {
            "video": video.pk,
            "variant": "dash",
            "filename": "manifest.mpd",
            "profileRevision": 2,
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["profileRevision"] == 2
    assert VideoFile.objects.get(pk=response.json()["id"]).profile_revision == 2


def test_the_stale_lookup_catches_superseded_and_pre_tracking_files(video: Video) -> None:
    """Acceptance criterion 4, and the reason the column is NOT NULL.

    A nullable column would have made this `< 2` drop the pre-tracking
    rows under three-valued logic -- and those are the bulk of the work,
    since every ladder built before the segment alignment was fixed is
    among them.
    """
    pre_tracking = make_file(video, VideoFileVariant.DASH, profile_revision=0)
    superseded = make_file(video, VideoFileVariant.THEORA, profile_revision=1)
    make_file(video, VideoFileVariant.WEBM_MED, profile_revision=2)
    make_file(video, VideoFileVariant.VC1, profile_revision=3)

    response = APIClient().get(reverse("api-videofile-list") + "?profile_revision__lt=2")

    assert response.status_code == 200
    assert filenames(response) == {pre_tracking.filename, superseded.filename}


def test_the_planning_query_narrows_to_one_variant(video: Video) -> None:
    """What ingest actually asks: the stale files of a single variant,
    because rebuilding a DASH ladder says nothing about the thumbnails."""
    stale_dash = make_file(video, VideoFileVariant.DASH, profile_revision=1)
    make_file(video, VideoFileVariant.THEORA, profile_revision=1)
    make_file(video, VideoFileVariant.LARGE_THUMB, profile_revision=0)

    response = APIClient().get(
        reverse("api-videofile-list") + "?variant=dash&profile_revision__lt=2"
    )

    assert filenames(response) == {stale_dash.filename}


def test_an_exact_revision_is_filterable_too(video: Video) -> None:
    current = make_file(video, VideoFileVariant.DASH, profile_revision=2)
    make_file(video, VideoFileVariant.THEORA, profile_revision=1)

    response = APIClient().get(reverse("api-videofile-list") + "?profile_revision=2")

    assert filenames(response) == {current.filename}
