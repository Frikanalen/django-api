# Shared test utilities for agenda.jukebox tests
import datetime
from unittest.mock import create_autospec
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from fk.models.video import Video
from fk.models.schedule import Scheduleitem
from fk.models.organization import Organization


def make_mock_picker():
    """Return a mock picker that always picks the first candidate."""

    def mock_pick(window, candidates, current_schedule, now):
        return candidates[0]

    return SimpleNamespace(
        pick=mock_pick,
    )


# Fake helpers for deterministic time logic in tests
def fake_round_up_to_5min_boundary(dt):
    return dt + datetime.timedelta(minutes=5)


def fake_interval_duration_sec(gap):
    return (gap.upper - gap.lower).total_seconds()


def make_mock_video(
    id=None,
    duration=datetime.timedelta(minutes=10),
    created_time=None,
    uploaded_time=None,
    organization=None,
    creator=None,
    name=None,
    **kwargs,
):
    """Create and save a real Video instance for testing."""
    User = get_user_model()
    if creator is None:
        creator, _ = User.objects.get_or_create(
            email="testuser@example.com", defaults={"first_name": "Test", "last_name": "User"}
        )
    if organization is None:
        organization, _ = Organization.objects.get_or_create(
            name="Test Org", defaults={"description": "desc"}
        )
    video = Video(
        duration=duration,
        created_time=created_time or datetime.datetime(2025, 1, 1),
        uploaded_time=uploaded_time,
        organization=organization,
        creator=creator,
        name=name or f"Test Video {id or ''}",
        **kwargs,
    )
    video.save()
    return video


def make_mock_scheduleitem(
    video=None,
    schedulereason=None,
    starttime=None,
    duration=None,
    endtime=None,
):
    mock_sched = create_autospec(Scheduleitem, instance=True)
    mock_sched.video = video
    mock_sched.schedulereason = schedulereason or Scheduleitem.REASON_JUKEBOX
    mock_sched.starttime = starttime or datetime.datetime(2025, 1, 1)
    mock_sched.duration = duration or datetime.timedelta(minutes=10)
    mock_sched.endtime = endtime or (mock_sched.starttime + mock_sched.duration)
    return mock_sched
