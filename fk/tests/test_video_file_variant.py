"""
The variants a VideoFile may take, now that they are an enum rather than
rows in a lookup table.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError

from fk.models import Organization, User, Video, VideoFile, VideoFileVariant

pytestmark = pytest.mark.django_db

MEDIA = "https://frikanalen.no/media/"


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="file-variant-editor@example.test")
    organization = Organization.objects.create(name="File variant org", editor=editor)
    return Video.objects.create(
        name="Streaming test video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=5),
        proper_import=True,
    )


def test_a_dash_manifest_resolves_under_its_own_directory(video: Video) -> None:
    manifest = VideoFile.objects.create(
        video=video,
        variant=VideoFileVariant.DASH,
        filename="manifest.mpd",
    )

    # The stored value is the name itself, so no row has to exist first.
    assert manifest.variant == "dash"
    assert manifest.location(relative=True) == f"{video.pk}/dash/manifest.mpd"
    assert video.videofile_url(VideoFileVariant.DASH) == f"{video.pk}/dash/manifest.mpd"


def test_an_unlisted_variant_is_rejected(video: Video) -> None:
    with pytest.raises(ValidationError):
        VideoFile(video=video, variant="hls", filename="master.m3u8").full_clean()


def test_mime_types_are_declared_where_we_have_an_answer() -> None:
    assert {variant: variant.mime_type for variant in VideoFileVariant} == {
        VideoFileVariant.LARGE_THUMB: "image/jpeg",
        VideoFileVariant.BROADCAST: "video/DV",
        VideoFileVariant.VC1: "video/vc1",
        VideoFileVariant.MED_THUMB: "image/jpeg",
        VideoFileVariant.SMALL_THUMB: "image/jpeg",
        # An original upload can be any accepted media type, so only the
        # generic binary type is safe to infer from its variant.
        VideoFileVariant.ORIGINAL: "application/octet-stream",
        VideoFileVariant.THEORA: "video/ogg",
        VideoFileVariant.SRT: "application/x-subrip",
        # An identifier stored in the filename column is not media.
        VideoFileVariant.CLOUDFLARE_ID: None,
        VideoFileVariant.DASH: "application/dash+xml",
        VideoFileVariant.WEBM_MED: "video/webm",
    }


def test_only_directly_playable_variants_are_published_to_vod(video: Video) -> None:
    VideoFile.objects.create(video=video, variant=VideoFileVariant.DASH, filename="manifest.mpd")
    VideoFile.objects.create(video=video, variant=VideoFileVariant.THEORA, filename="video.ogv")

    # A manifest needs a player to interpret it, so it is not a source
    # vod_files() can hand to a <video> element.
    assert video.vod_files() == [
        {"url": f"{MEDIA}{video.pk}/theora/video.ogv", "mime_type": "video/ogg"}
    ]
