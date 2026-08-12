"""
SchedulePurpose picks the videos that fill WeeklySlots; the automatic
scheduler is built on videos_queryset() and single_video(), which had
no coverage at all.
"""

from datetime import UTC, datetime, timedelta

import pytest

from fk.models import Organization, Scheduleitem, SchedulePurpose, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def organization() -> Organization:
    editor = User.objects.create(email="purpose-editor@example.test")
    return Organization.objects.create(name="Purpose org", editor=editor)


def make_video(organization: Organization, name: str, **overrides) -> Video:
    fields = {
        "name": name,
        "creator": organization.editor,
        "organization": organization,
        "duration": timedelta(minutes=30),
        "proper_import": True,
        **overrides,
    }
    return Video.objects.create(**fields)


def org_purpose(organization: Organization, strategy: str = "latest") -> SchedulePurpose:
    return SchedulePurpose.objects.create(
        name="Org purpose",
        type=SchedulePurpose.TYPE.organization,
        strategy=strategy,
        organization=organization,
    )


def test_organization_purpose_uses_the_organizations_proper_videos(organization) -> None:
    listed = make_video(organization, "Proper video")
    make_video(organization, "Broken video", proper_import=False)

    assert list(org_purpose(organization).videos_queryset()) == [listed]


def test_videos_purpose_uses_directly_connected_videos(organization) -> None:
    direct = make_video(organization, "Direct video")
    make_video(organization, "Unconnected video")
    purpose = SchedulePurpose.objects.create(
        name="Direct purpose",
        type=SchedulePurpose.TYPE.videos,
        strategy="latest",
    )
    purpose.direct_videos.add(direct)

    assert list(purpose.videos_queryset()) == [direct]


def test_videos_queryset_can_cap_duration(organization) -> None:
    fits = make_video(organization, "Short video", duration=timedelta(minutes=10))
    make_video(organization, "Long video", duration=timedelta(minutes=45))

    result = org_purpose(organization).videos_queryset(max_duration=timedelta(minutes=15))

    assert list(result) == [fits]


def test_unhandled_type_raises(organization) -> None:
    purpose = SchedulePurpose(name="Broken", type="nonsense", strategy="latest")

    with pytest.raises(ValueError, match="Unhandled type"):
        purpose.videos_queryset()


def created_at(video: Video, when: datetime) -> Video:
    """Backdate a record. created_time is auto_now_add, so it can only be
    set past the model by writing straight to the row."""
    Video.objects.filter(pk=video.pk).update(created_time=when)
    video.refresh_from_db()
    return video


def test_latest_strategy_returns_the_most_recently_created(organization) -> None:
    created_at(make_video(organization, "Old record"), datetime(2015, 1, 1, tzinfo=UTC))
    newest = created_at(make_video(organization, "New record"), datetime(2016, 1, 1, tzinfo=UTC))

    assert org_purpose(organization, "latest").single_video() == newest


def test_latest_strategy_ignores_uploaded_time(organization) -> None:
    """uploaded_time describes the media file and is nullable. Ordering by
    it put rows that never carried one ahead of every real upload on
    PostgreSQL, which sorts NULLs first descending."""
    created_at(
        make_video(organization, "No upload timestamp", uploaded_time=None),
        datetime(2015, 1, 1, tzinfo=UTC),
    )
    newest = created_at(
        make_video(
            organization, "Uploaded long ago", uploaded_time=datetime(2010, 1, 1, tzinfo=UTC)
        ),
        datetime(2016, 1, 1, tzinfo=UTC),
    )

    assert org_purpose(organization, "latest").single_video() == newest


def test_latest_strategy_breaks_timestamp_ties_deterministically(organization) -> None:
    """Bulk imports land many records on one timestamp; without the
    tiebreak the backend is free to return either."""
    same_moment = datetime(2015, 1, 1, tzinfo=UTC)
    created_at(make_video(organization, "First of the batch"), same_moment)
    last = created_at(make_video(organization, "Last of the batch"), same_moment)

    assert org_purpose(organization, "latest").single_video() == last


def test_latest_strategy_returns_none_when_empty(organization) -> None:
    assert org_purpose(organization, "latest").single_video() is None


def test_random_strategy_picks_from_the_queryset(organization) -> None:
    only = make_video(organization, "Only video")

    assert org_purpose(organization, "random").single_video() == only


def test_least_scheduled_strategy_prefers_the_least_played(organization) -> None:
    scheduled = make_video(organization, "Scheduled often")
    Scheduleitem.objects.create(
        video=scheduled,
        starttime=datetime(2015, 1, 1, 10, tzinfo=UTC),
        duration=timedelta(minutes=30),
        schedulereason=Scheduleitem.REASON_AUTO,
    )
    never_scheduled = make_video(organization, "Never scheduled")

    result = org_purpose(organization, "least_scheduled").single_video()

    assert result == never_scheduled


def test_unhandled_strategy_raises(organization) -> None:
    purpose = org_purpose(organization, "latest")
    purpose.strategy = "nonsense"

    with pytest.raises(ValueError, match="Unhandled strategy"):
        purpose.single_video()
