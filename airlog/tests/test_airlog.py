"""The air log: coverage, playlists and segments from the recorder's folder."""

import datetime

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from airlog import segments
from fk.models import User

pytestmark = pytest.mark.django_db
UTC = datetime.UTC
T0 = datetime.datetime(2026, 11, 25, 15, 59, 40, tzinfo=UTC)


def write(base, start: datetime.datetime, count: int, step: int = 10) -> None:
    for i in range(count):
        t = start + datetime.timedelta(seconds=i * step)
        folder = base / f"{t:%Y-%m-%d}" / f"{t:%H}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{t:%Y%m%dT%H%M%S}Z.ts").write_bytes(b"G" * 188)


@pytest.fixture
def airlog_dir(settings, tmp_path):
    settings.FK_AIRLOG_DIR = str(tmp_path)
    settings.FK_AIRLOG_SEGMENT_SECONDS = 10
    # 15:59:40-16:00:40 (across an hour folder), then a gap, then 16:01:30-16:02:00.
    write(tmp_path, T0, 6)
    write(tmp_path, datetime.datetime(2026, 11, 25, 16, 1, 30, tzinfo=UTC), 3)
    (tmp_path / "2026-11-25" / "16" / "20261125T160300Z.ts.tmp").write_bytes(b"partial")
    (tmp_path / "live.m3u8").write_text("#EXTM3U\n")
    return tmp_path


@pytest.fixture
def staff():
    client = APIClient()
    client.force_authenticate(User.objects.create(email="staff@example.test", is_superuser=True))
    return client


def at(hour, minute, second=0):
    return datetime.datetime(2026, 11, 25, hour, minute, second, tzinfo=UTC)


def test_segments_cross_hour_folders_and_mark_the_gap(airlog_dir):
    found = segments.segments(at(15, 59), at(16, 5))
    assert [s.start for s in found][:2] == [T0, T0 + datetime.timedelta(seconds=10)]
    assert len(found) == 9  # the .tmp segment is still being written
    assert [s.discontinuity for s in found] == [True] + [False] * 5 + [True, False, False]
    assert all(s.duration == 10 for s in found)


def test_spans_leave_the_gap_out(airlog_dir):
    assert segments.spans(at(15, 0), at(17, 0)) == [
        (T0, at(16, 0, 40)),
        (at(16, 1, 30), at(16, 2, 0)),
    ]
    # Clipped to the range asked for.
    assert segments.spans(at(16, 0), at(16, 1, 40)) == [
        (at(16, 0), at(16, 0, 40)),
        (at(16, 1, 30), at(16, 1, 40)),
    ]


def test_playlist_of_a_finished_stretch_ends(airlog_dir):
    text = segments.playlist(at(15, 59, 55), at(16, 2), now=at(18, 0))
    lines = text.splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[-1] == "#EXT-X-ENDLIST"
    # The segment that began 15:59:50 still runs into the stretch.
    assert lines[5:8] == [
        "#EXT-X-PROGRAM-DATE-TIME:2026-11-25T15:59:50.000Z",
        "#EXTINF:10.000,",
        "segments/2026-11-25/15/20261125T155950Z.ts",
    ]
    assert lines.count("#EXT-X-DISCONTINUITY") == 1
    assert lines[lines.index("#EXT-X-DISCONTINUITY") + 1] == (
        "#EXT-X-PROGRAM-DATE-TIME:2026-11-25T16:01:30.000Z"
    )


def test_playlist_running_up_to_now_stays_open(airlog_dir):
    text = segments.playlist(at(16, 0), at(16, 30), now=at(16, 2, 5))
    assert "#EXT-X-ENDLIST" not in text


def test_staff_only(airlog_dir):
    url = reverse("api-airlog-coverage") + "?start=2026-11-25T15:00:00Z&end=2026-11-25T17:00:00Z"
    assert APIClient().get(url).status_code in (401, 403)
    member = APIClient()
    member.force_authenticate(User.objects.create(email="member@example.test"))
    assert member.get(url).status_code == 403
    segment = reverse("api-airlog-segment", args=["2026-11-25/16/20261125T160000Z.ts"])
    assert member.get(segment).status_code == 403


def test_coverage(airlog_dir, staff):
    url = reverse("api-airlog-coverage")
    response = staff.get(
        url, {"start": "2026-11-25T16:00:00+01:00", "end": "2026-11-25T18:00:00+01:00"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["segmentSeconds"] == 10
    assert body["spans"] == [
        {"start": "2026-11-25T15:59:40Z", "end": "2026-11-25T16:00:40Z"},
        {"start": "2026-11-25T16:01:30Z", "end": "2026-11-25T16:02:00Z"},
    ]


@pytest.mark.parametrize(
    "query",
    [
        {"start": "2026-11-25T16:00:00Z"},
        {"start": "2026-11-25T16:00:00", "end": "2026-11-25T17:00:00"},
        {"start": "2026-11-25T17:00:00Z", "end": "2026-11-25T16:00:00Z"},
        {"start": "2026-11-20T00:00:00Z", "end": "2026-11-25T00:00:00Z"},
    ],
)
def test_coverage_rejects_bad_ranges(airlog_dir, staff, query):
    assert staff.get(reverse("api-airlog-coverage"), query).status_code == 400


def test_playlist_endpoint(airlog_dir, staff):
    response = staff.get(
        reverse("api-airlog-playlist"),
        {"start": "2026-11-25T16:00:00Z", "end": "2026-11-25T16:02:00Z"},
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/vnd.apple.mpegurl"
    assert response["Cache-Control"] == "no-store"
    assert b"segments/2026-11-25/16/20261125T160000Z.ts" in response.content
    long = staff.get(
        reverse("api-airlog-playlist"),
        {"start": "2026-11-25T08:00:00Z", "end": "2026-11-25T16:00:00Z"},
    )
    assert long.status_code == 400


def test_segment_endpoint(airlog_dir, staff):
    ok = staff.get(reverse("api-airlog-segment", args=["2026-11-25/16/20261125T160000Z.ts"]))
    assert ok.status_code == 200
    assert ok["Content-Type"] == "video/mp2t"
    assert b"".join(ok.streaming_content) == b"G" * 188
    for path in (
        "2026-11-25/16/20261125T165900Z.ts",  # not recorded
        "2026-11-25/16/20261125T160300Z.ts.tmp",  # still being written
        "../../etc/passwd",
        "live.m3u8",
    ):
        assert staff.get(f"/api/airlog/segments/{path}").status_code == 404


def test_not_set_up(settings, staff):
    settings.FK_AIRLOG_DIR = ""
    response = staff.get(
        reverse("api-airlog-coverage"),
        {"start": "2026-11-25T16:00:00Z", "end": "2026-11-25T17:00:00Z"},
    )
    assert response.status_code == 404
