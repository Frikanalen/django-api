"""
Video model helpers: URL resolution for media files, the manager
querysets, and the small presentation helpers used by templates and
serializers.
"""

from datetime import timedelta

import pytest

from fk.models import Organization, User, Video, VideoFile, VideoFileVariant

pytestmark = pytest.mark.django_db

MEDIA = "https://frikanalen.no/media/"


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="video-model-editor@example.test")


@pytest.fixture
def organization(editor: User) -> Organization:
    return Organization.objects.create(name="Video model org", fkmember=True, editor=editor)


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Model test video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=5),
        proper_import=True,
    )


def add_file(video: Video, variant: VideoFileVariant, filename: str) -> VideoFile:
    return VideoFile.objects.create(video=video, variant=variant, filename=filename)


def test_a_files_location_keeps_only_the_basename(video: Video) -> None:
    video_file = add_file(video, VideoFileVariant.BROADCAST, "some/path/master.avi")

    # The stored filename may carry a path; the media layout does not.
    assert video_file.location(relative=True) == f"{video.pk}/broadcast/master.avi"


def test_public_queryset_requires_web_publishing_and_proper_import(
    editor: User, organization: Organization, video: Video
) -> None:
    Video.objects.create(
        name="Unpublished",
        creator=editor,
        organization=organization,
        proper_import=True,
        publish_on_web=False,
    )
    Video.objects.create(
        name="Improper",
        creator=editor,
        organization=organization,
        proper_import=False,
        publish_on_web=True,
    )

    assert list(Video.objects.public()) == [video]


def test_fillers_queryset_applies_all_four_criteria(
    editor: User, organization: Organization
) -> None:
    def filler(name: str, **overrides) -> Video:
        fields = {
            "name": name,
            "creator": editor,
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
