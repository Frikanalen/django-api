"""
The names a FileFormat may take, and the 'dash' row that migration 0024
puts in every database.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError

from fk.models import FileFormat, Organization, User, Video, VideoFile

pytestmark = pytest.mark.django_db

MEDIA = "https://frikanalen.no/media/"


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="file-format-editor@example.test")
    organization = Organization.objects.create(name="File format org", editor=editor)
    return Video.objects.create(
        name="Streaming test video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=5),
        proper_import=True,
    )


def test_dash_row_ships_with_the_schema() -> None:
    # Created by the data migration, so ingest can register a manifest
    # without anyone adding the format by hand first.
    dash = FileFormat.objects.get(fsname="dash")

    assert dash.mime_type == "application/dash+xml"
    # A manifest is not a source a <video> element can play unaided; see
    # the migration for why it stays out of vod_files().
    assert dash.vod_publish is False
    # And 'dash' is a permitted fsname, not just a row that got past the
    # choices by being written through the ORM.
    dash.full_clean()


def test_an_unlisted_fsname_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FileFormat(fsname="hls").full_clean()


def test_a_dash_manifest_resolves_under_its_own_directory(video: Video) -> None:
    manifest = VideoFile.objects.create(
        video=video,
        format=FileFormat.objects.get(fsname="dash"),
        filename="manifest.mpd",
    )

    assert manifest.location(relative=True) == f"{video.pk}/dash/manifest.mpd"
    assert video.videofile_url("dash") == f"{video.pk}/dash/manifest.mpd"
