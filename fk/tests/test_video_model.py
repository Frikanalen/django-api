"""
Video model helpers: URL resolution for media files, the manager
querysets, and the small presentation helpers used by templates and
serializers.
"""

from datetime import timedelta

import pytest

from fk.models import FileFormat, Organization, User, Video, VideoFile

pytestmark = pytest.mark.django_db

MEDIA = "https://upload.frikanalen.no/media/"


@pytest.fixture
def organization() -> Organization:
    editor = User.objects.create(email="video-model-editor@example.test")
    organization = Organization.objects.create(name="Video model org", fkmember=True, editor=editor)
    return organization


@pytest.fixture
def video(organization: Organization) -> Video:
    return Video.objects.create(
        name="Model test video",
        creator=organization.editor,
        organization=organization,
        duration=timedelta(minutes=5),
        proper_import=True,
    )


def add_file(video: Video, fsname: str, filename: str, **format_fields) -> VideoFile:
    file_format = FileFormat.objects.create(fsname=fsname, **format_fields)
    return VideoFile.objects.create(video=video, format=file_format, filename=filename)


def test_videofile_url_is_the_relative_location(video: Video) -> None:
    add_file(video, "broadcast", "some/path/master.avi")

    # location() keeps only the basename of the stored filename.
    assert video.videofile_url("broadcast") == f"{video.pk}/broadcast/master.avi"


def test_thumbnail_urls_resolve_to_the_media_host(video: Video) -> None:
    add_file(video, "small_thumb", "small.jpg")
    add_file(video, "large_thumb", "large.jpg")

    assert video.small_thumbnail_url() == f"{MEDIA}{video.pk}/small_thumb/small.jpg"
    assert video.large_thumbnail_url() == f"{MEDIA}{video.pk}/large_thumb/large.jpg"


def test_thumbnail_urls_fall_back_to_static_defaults(video: Video) -> None:
    assert video.small_thumbnail_url() == "/static/default_small_thumbnail.png"
    assert video.medium_thumbnail_url() == "/static/default_medium_thumbnail.png"
    assert video.large_thumbnail_url() == "/static/default_large_thumbnail.png"


def test_medium_thumbnail_never_resolves_for_choice_compliant_data(video: Video) -> None:
    """
    Pinned quirk: medium_thumbnail_url() looks for fsname
    'medium_thumb', but the FileFormat choices only offer 'med_thumb',
    so data that respects the choices always falls back to the static
    default.
    """
    add_file(video, "med_thumb", "medium.jpg")

    assert video.medium_thumbnail_url() == "/static/default_medium_thumbnail.png"


def test_ogv_url(video: Video) -> None:
    assert video.ogv_url() is None

    add_file(video, "theora", "video.ogv")
    assert video.ogv_url() == f"{MEDIA}{video.pk}/theora/video.ogv"


def test_vod_files_lists_only_publishable_formats(video: Video) -> None:
    add_file(video, "original", "master.mp4")
    add_file(video, "theora", "video.ogv", vod_publish=True, mime_type="video/ogg")

    assert video.vod_files() == [
        {"url": f"{MEDIA}{video.pk}/theora/video.ogv", "mime_type": "video/ogg"}
    ]


def test_public_queryset_requires_web_publishing_and_proper_import(
    organization: Organization, video: Video
) -> None:
    Video.objects.create(
        name="Unpublished",
        creator=organization.editor,
        organization=organization,
        proper_import=True,
        publish_on_web=False,
    )
    Video.objects.create(
        name="Improper",
        creator=organization.editor,
        organization=organization,
        proper_import=False,
        publish_on_web=True,
    )

    assert list(Video.objects.public()) == [video]


def test_fillers_queryset_applies_all_four_criteria(organization: Organization) -> None:
    def filler(name: str, **overrides) -> Video:
        fields = {
            "name": name,
            "creator": organization.editor,
            "organization": organization,
            "is_filler": True,
            "has_tono_records": False,
            "proper_import": True,
            **overrides,
        }
        return Video.objects.create(**fields)

    eligible = filler("Eligible")
    filler("Not a filler", is_filler=False)
    filler("Tono encumbered", has_tono_records=True)
    filler("Broken import", proper_import=False)
    outside_editor = User.objects.create(email="nonmember-editor@example.test")
    outside = Organization.objects.create(
        name="Non-member org", fkmember=False, editor=outside_editor
    )
    filler("Wrong org", organization=outside)

    assert list(Video.objects.fillers()) == [eligible]


def test_presentation_helpers(video: Video) -> None:
    assert video.is_public()
    assert video.tags() == "www"

    video.has_tono_records = True
    video.is_filler = True
    assert video.tags() == "tono, www, filler"

    video.publish_on_web = False
    assert not video.is_public()
    assert video.get_absolute_url() == f"/video/{video.pk}/"
