"""
Tests for the gap detection function.

These tests verify the core gap detection logic without a database.
"""

import datetime

from portion import closedopen

from agenda.jukebox.find_schedule_gaps import find_schedule_gaps


class MockScheduledItem:
    """Mock scheduled item for testing, implements the ScheduledItem protocol."""

    def __init__(self, starttime: datetime.datetime, duration: datetime.timedelta):
        self.starttime = starttime
        self.duration = duration

    def endtime(self) -> datetime.datetime:
        return self.starttime + self.duration


class TestGapDetection:
    """Test suite for gap detection algorithm."""

    def setup_method(self):
        """Set up test fixtures."""
        # Base time for all tests (arbitrary datetime)
        self.base = datetime.datetime(2025, 1, 1, 10, 0, 0)

    def test_no_items_full_gap(self):
        """Test that with no scheduled items, the entire window is a gap."""
        start = self.base
        end = self.base + datetime.timedelta(hours=2)
        window = closedopen(start, end)

        gaps = find_schedule_gaps(window, [])

        assert len(gaps) == 1
        assert gaps[0].lower == start
        assert gaps[0].upper == end

    def test_single_item_creates_gaps_before_and_after(self):
        """Test that a single item creates gaps before and after it."""
        start = self.base
        end = self.base + datetime.timedelta(hours=3)
        window = closedopen(start, end)

        # Item in the middle: 1 hour duration starting at 1 hour mark
        item = MockScheduledItem(
            starttime=self.base + datetime.timedelta(hours=1), duration=datetime.timedelta(hours=1)
        )

        gaps = find_schedule_gaps(window, [item])

        assert len(gaps) == 2
        # First gap: from start to item start
        assert gaps[0].lower == start
        assert gaps[0].upper == item.starttime
        # Second gap: from item end to window end
        assert gaps[1].lower == item.endtime
        assert gaps[1].upper == end

    def test_minimum_gap_duration_enforced(self):
        """Test that gaps smaller than minimum_gap_seconds are not included."""
        start = self.base
        end = self.base + datetime.timedelta(seconds=600)  # 10 minutes
        window = closedopen(start, end)

        # Item that leaves only 4 minutes (240 seconds) gaps on each side
        item = MockScheduledItem(
            starttime=self.base + datetime.timedelta(seconds=240),
            duration=datetime.timedelta(seconds=120),
        )

        # Use default minimum of 300 seconds
        gaps = find_schedule_gaps(window, [item])

        # Both 4-minute gaps should be filtered out (minimum is 5 minutes)
        assert len(gaps) == 0

    def test_overlapping_items_handled_correctly(self):
        """Test that overlapping items don't create negative gaps."""
        start = self.base
        end = self.base + datetime.timedelta(hours=3)
        window = closedopen(start, end)

        items = [
            MockScheduledItem(
                starttime=self.base + datetime.timedelta(hours=1),
                duration=datetime.timedelta(hours=1),
            ),
            MockScheduledItem(
                starttime=self.base + datetime.timedelta(hours=1, minutes=30),
                duration=datetime.timedelta(hours=1),
            ),
        ]

        gaps = find_schedule_gaps(window, items)

        # Should have: gap before first item, then nothing (overlap), gap after second item
        assert len(gaps) == 2
        assert gaps[0].lower == start
        assert gaps[0].upper == items[0].starttime
        assert gaps[1].lower == items[1].endtime
        assert gaps[1].upper == end

    def test_items_completely_outside_window(self):
        """Test that items before the window don't affect gap calculation."""
        start = self.base + datetime.timedelta(hours=2)
        end = self.base + datetime.timedelta(hours=3)
        window = closedopen(start, end)

        # Item that ends exactly at window start
        item = MockScheduledItem(starttime=self.base, duration=datetime.timedelta(hours=2))

        gaps = find_schedule_gaps(window, [item])

        # Item is skipped, entire window is a gap
        assert len(gaps) == 1
        assert gaps[0].lower == start
        assert gaps[0].upper == end

    def test_items_exactly_at_boundaries(self):
        """Test edge case where items start or end at window boundaries."""
        start = self.base
        end = self.base + datetime.timedelta(hours=2)
        window = closedopen(start, end)

        # Item exactly fills the window
        item = MockScheduledItem(starttime=start, duration=datetime.timedelta(hours=2))

        gaps = find_schedule_gaps(window, [item])

        # No gaps when item fills the window
        assert len(gaps) == 0

    def test_multiple_items_with_gaps(self):
        """Test realistic scenario with multiple scheduled items."""
        start = self.base
        end = self.base + datetime.timedelta(hours=4)
        window = closedopen(start, end)

        items = [
            MockScheduledItem(
                starttime=self.base + datetime.timedelta(minutes=30),
                duration=datetime.timedelta(minutes=30),
            ),
            MockScheduledItem(
                starttime=self.base + datetime.timedelta(hours=1, minutes=30),
                duration=datetime.timedelta(hours=1),
            ),
            MockScheduledItem(
                starttime=self.base + datetime.timedelta(hours=3),
                duration=datetime.timedelta(minutes=30),
            ),
        ]

        gaps = find_schedule_gaps(window, items)

        # Should have: gap at start, gap between first and second items, gap between second and third, gap at end
        assert len(gaps) == 4
