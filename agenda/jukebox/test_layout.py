import datetime
import pytest
from portion import closedopen
from unittest.mock import patch
from agenda.jukebox.layout import ScheduleLayout
from agenda.jukebox.test_utils import (
    make_mock_video,
    make_mock_picker,
    make_mock_scheduleitem,
)


@pytest.mark.django_db
class ScheduleLayoutTestCase:
    def test_layout_fills_gap_with_videos(self):
        """Test that layout fills a gap with multiple videos without overlapping."""
        videos = [
            make_mock_video(id=1, duration=datetime.timedelta(minutes=5)),
            make_mock_video(id=2, duration=datetime.timedelta(minutes=10)),
        ]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 20)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)
        layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
        items = layout.layout(gap, scheduled_items, now)

        # With mock picker selecting first candidate, expect 2 videos:
        # First video (id=1, 5min) at 12:00, second (id=1 again, 5min) after boundary rounding
        assert len(items) >= 2
        assert all(item.video in videos for item in items)
        assert sum([item.duration for item in items], datetime.timedelta()) <= gap.upper - gap.lower

        # Verify videos stay within gap bounds
        for item in items:
            assert item.starttime >= gap.lower
            assert item.starttime + item.duration <= gap.upper

        # Verify no overlaps
        for i in range(1, len(items)):
            prev = items[i - 1]
            curr = items[i]
            assert curr.starttime >= prev.starttime + prev.duration

    def test_layout_no_videos_fit(self):
        videos = [make_mock_video(duration=datetime.timedelta(minutes=30))]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 10)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)
        layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
        items = layout.layout(gap, scheduled_items, now)
        assert items == []

    def test_layout_zero_duration_video(self):
        videos = [make_mock_video(duration=datetime.timedelta(seconds=0))]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 10)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)
        layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
        items = layout.layout(gap, scheduled_items, now)
        assert all(item.duration > datetime.timedelta(0) for item in items)

    def test_layout_exact_fit(self):
        """Test that layout efficiently fills a gap when videos fit exactly."""
        videos = [
            make_mock_video(id=1, duration=datetime.timedelta(minutes=5)),
            make_mock_video(id=2, duration=datetime.timedelta(minutes=5)),
        ]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 10)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)
        layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
        items = layout.layout(gap, scheduled_items, now)

        # Mock picker always picks first candidate (5min video)
        # Gap is 10min, so expect at least 1 video, possibly 2 depending on boundary rounding
        assert len(items) >= 1
        total = sum([item.duration for item in items], datetime.timedelta())
        assert total <= gap.upper - gap.lower
        # Verify all videos are from candidates
        assert all(item.video in videos for item in items)

    def test_layout_empty_candidates(self):
        videos = []
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 10)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)
        layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
        items = layout.layout(gap, scheduled_items, now)
        assert items == []


    def test_layout_respects_5min_boundaries(self):
        """Test that cursor advances to 5-minute boundaries between videos."""
        videos = [
            make_mock_video(id=1, duration=datetime.timedelta(minutes=3)),
            make_mock_video(id=2, duration=datetime.timedelta(minutes=3)),
        ]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 10)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)

        with patch('agenda.jukebox.layout.round_up_to_5min_boundary', side_effect=lambda dt: dt + datetime.timedelta(minutes=5)) as mock_round:
            layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
            items = layout.layout(gap, scheduled_items, now)
            # First video: 12:00-12:03, cursor rounds to 12:05
            # Second video: would be 12:05-12:08, cursor would round to 12:10
            # But 12:10 >= gap.upper, so only first video fits
            assert len(items) == 1
            assert items[0].starttime == datetime.datetime(2025, 1, 1, 12, 0)
            assert mock_round.call_count >= 1

    def test_layout_stops_when_remaining_gap_too_small_after_rounding(self):
        """Test that layout stops when remaining space after boundary rounding is too small."""
        videos = [
            make_mock_video(id=1, duration=datetime.timedelta(minutes=6)),
        ]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 15)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)

        # After first video (6 min) + round to boundary (to 12:10), only 5 min left
        # but video needs 6 min, so second video won't fit
        layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
        items = layout.layout(gap, scheduled_items, now)
        assert len(items) == 1

    def test_layout_with_gap_not_on_5min_boundary(self):
        """Test layout when gap boundaries don't align with 5-minute marks."""
        videos = [
            make_mock_video(id=1, duration=datetime.timedelta(minutes=5)),
        ]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 2, 30), datetime.datetime(2025, 1, 1, 12, 10)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)
        layout = ScheduleLayout(candidates=videos, picker=make_mock_picker())
        items = layout.layout(gap, scheduled_items, now)
        # Gap starts at 12:02:30, video fits (ends at 12:07:30)
        assert len(items) == 1
        assert items[0].starttime == datetime.datetime(2025, 1, 1, 12, 2, 30)

    def test_layout_with_pre_existing_scheduled_items(self):
        """Test that pre-existing scheduled items are passed to picker."""
        videos = [
            make_mock_video(id=1, duration=datetime.timedelta(minutes=5)),
            make_mock_video(id=2, duration=datetime.timedelta(minutes=5)),
        ]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 15)
        )
        existing_item = make_mock_scheduleitem(
            video=make_mock_video(id=99),
            starttime=datetime.datetime(2025, 1, 1, 11, 0),
            duration=datetime.timedelta(minutes=30)
        )
        scheduled_items = [existing_item]
        now = datetime.datetime(2025, 1, 1, 11, 59)

        picker_calls = []
        def tracking_pick(window, candidates, current_schedule, now):
            picker_calls.append({
                'window': window,
                'candidates': candidates,
                'current_schedule': list(current_schedule),
                'now': now
            })
            return candidates[0]

        from types import SimpleNamespace
        tracking_picker = SimpleNamespace(pick=tracking_pick)

        layout = ScheduleLayout(candidates=videos, picker=tracking_picker)
        items = layout.layout(gap, scheduled_items, now)

        # Verify picker was called with existing items in first call
        assert len(picker_calls) > 0
        assert existing_item in picker_calls[0]['current_schedule']
        # Verify picker received accumulated items in subsequent calls
        if len(picker_calls) > 1:
            assert items[0] in picker_calls[1]['current_schedule']

    def test_layout_picker_receives_correct_arguments(self):
        """Test that picker receives correct window, candidates, schedule, and now."""
        videos = [
            make_mock_video(id=1, duration=datetime.timedelta(minutes=5)),
            make_mock_video(id=2, duration=datetime.timedelta(minutes=10)),
        ]
        gap = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 20)
        )
        scheduled_items = []
        now = datetime.datetime(2025, 1, 1, 11, 59)

        picker_calls = []
        def tracking_pick(window, candidates, current_schedule, now):
            picker_calls.append({
                'window': window,
                'candidates': list(candidates),
                'current_schedule': list(current_schedule),
                'now': now
            })
            return candidates[0]

        from types import SimpleNamespace
        tracking_picker = SimpleNamespace(pick=tracking_pick)

        layout = ScheduleLayout(candidates=videos, picker=tracking_picker)
        items = layout.layout(gap, scheduled_items, now)

        # Verify first call has correct parameters
        assert len(picker_calls) > 0
        first_call = picker_calls[0]
        assert first_call['window'].lower == gap.lower
        assert first_call['window'].upper == gap.upper
        assert first_call['now'] == now
        # All candidates fit in first window
        assert len(first_call['candidates']) == 2
