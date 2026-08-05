"""
Characterization tests for the jukebox CSV feed.

The playout side parses this output, so the exact bytes are the contract:
header names, the ``|`` delimiter, and the long-standing quirk that text
fields are rendered as the *repr* of their UTF-8 encoding (``b'...'``).
api.views.jukebox_csv keeps that quirk on purpose; these tests exist so a
refactor cannot "fix" it by accident.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.http import HttpResponse
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import FileFormat, Organization, User, Video, VideoFile

pytestmark = pytest.mark.django_db

HEADER = (
    "id|name|has_tono_records|video_id|type_id|version"
    "|creation_began|creation_finished|offset|duration|location"
)

CREATED = datetime(2015, 1, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def member_organization() -> Organization:
    editor = User.objects.create(email="jukebox-editor@example.test")
    return Organization.objects.create(name="Jukebox org", fkmember=True, editor=editor)


@pytest.fixture
def broadcast_format() -> FileFormat:
    return FileFormat.objects.create(fsname="broadcast")


def make_filler(
    organization: Organization,
    name: str = "Filler video",
    **overrides,
) -> Video:
    fields = {
        "name": name,
        "creator": organization.editor,
        "organization": organization,
        "duration": timedelta(minutes=1),
        "is_filler": True,
        "has_tono_records": False,
        **overrides,
    }
    video = Video.objects.create(**fields)
    # created_time is auto_now_add; pin it after the fact so the CSV body
    # is byte-for-byte reproducible.
    Video.objects.filter(pk=video.pk).update(created_time=CREATED)
    return video


def fetch_csv() -> tuple[str, HttpResponse]:
    response = APIClient().get(reverse("jukebox-csv"))
    return response.content.decode("utf-8"), response


def test_csv_row_is_byte_for_byte_stable(member_organization, broadcast_format) -> None:
    video = make_filler(member_organization, name="Måkeskrik")
    VideoFile.objects.create(video=video, format=broadcast_format, filename="måkeskrik.mp4")

    body, response = fetch_csv()

    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == "filename=jukebox.csv"
    # Note the quirks this pins: name and the location filename are the
    # Python repr of UTF-8 bytes, has_tono_records is 't'/'f', the empty
    # creation_finished column, and a float duration in seconds.
    assert body == (
        f"{HEADER}\r\n"
        f"{video.id}|b'M\\xc3\\xa5keskrik'|f|{video.id}|{broadcast_format.id}|1"
        f"|2015-01-01 10:00:00+00:00||0|60.0"
        f"|http://frontend.frikanalen.tv/media/b'm\\xc3\\xa5keskrik.mp4'\r\n"
    )


def test_fillers_without_a_broadcast_file_are_skipped(
    member_organization, broadcast_format
) -> None:
    make_filler(member_organization, name="No broadcast file")
    listed = make_filler(member_organization, name="Has broadcast file")
    VideoFile.objects.create(video=listed, format=broadcast_format, filename="listed.mp4")

    body, _ = fetch_csv()

    assert f"|{listed.id}|" in body
    assert "No broadcast file" not in body


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"is_filler": False}, id="not-a-filler"),
        pytest.param({"has_tono_records": True}, id="tono-encumbered"),
    ],
)
def test_ineligible_videos_are_excluded(member_organization, broadcast_format, overrides) -> None:
    video = make_filler(member_organization, name="Excluded video", **overrides)
    VideoFile.objects.create(video=video, format=broadcast_format, filename="excluded.mp4")

    body, _ = fetch_csv()

    assert body == f"{HEADER}\r\n"


def test_videos_from_non_member_organizations_are_excluded(broadcast_format) -> None:
    editor = User.objects.create(email="non-member-editor@example.test")
    non_member = Organization.objects.create(name="Non-member org", fkmember=False, editor=editor)
    video = make_filler(non_member, name="Non-member video")
    VideoFile.objects.create(video=video, format=broadcast_format, filename="nonmember.mp4")

    body, _ = fetch_csv()

    assert body == f"{HEADER}\r\n"
