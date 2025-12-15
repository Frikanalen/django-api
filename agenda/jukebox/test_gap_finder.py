"""
Tests for the GapFinder class demonstrating improved testability.

These tests show how the refactored code can be tested without a database.
"""

import datetime

from agenda.jukebox.gap_finder import GapFinder


class MockScheduledItem:
    """Mock scheduled item for testing, implements the ScheduledItem protocol."""

    def __init__(self, starttime: datetime.datetime, duration: datetime.timedelta):
        self.starttime = starttime
        self.duration = duration

    def endtime(self) -> datetime.datetime:
        return self.starttime + self.duration


class TestGapFinder:
    """Test suite for GapFinder algorithm."""

    def setup_method(self):
        """Set up test fixtures."""
        self.finder = GapFinder()
        # Base time for all tests (arbitrary datetime)
        self.base = datetime.datetime(2025, 1, 1, 10, 0, 0)

    def test_no_items_full_gap(self):
        """Test that with no scheduled items, the entire window is a gap."""
        start = self.base
        end = self.base + datetime.timedelta(hours=2)

        gaps = self.finder._find_gaps_in_items(start, end, [])

        assert len(gaps) == 1
        assert gaps[0].start == start
        assert gaps[0].end == end

    def test_single_item_creates_gaps_before_and_after(self):
        """Test that a single item creates gaps before and after it."""
        start = self.base
        end = self.base + datetime.timedelta(hours=3)

        # Item in the middle: 1 hour duration starting at 1 hour mark
        item = MockScheduledItem(
            starttime=self.base + datetime.timedelta(hours=1), duration=datetime.timedelta(hours=1)
        )

        gaps = self.finder._find_gaps_in_items(start, end, [item])

        assert len(gaps) == 2
        # First gap: from start to item start
        assert gaps[0].start == start
        assert gaps[0].end == item.starttime
        # Second gap: from item end to window end
        assert gaps[1].start == item.endtime()
        assert gaps[1].end == end

    def test_minimum_gap_duration_enforced(self):
        """Test that gaps smaller than MINIMUM_GAP_SECONDS are not included."""
        start = self.base
        end = self.base + datetime.timedelta(seconds=600)  # 10 minutes

        # Item that leaves only 4 minutes (240 seconds) gaps on each side
        item = MockScheduledItem(
            starttime=self.base + datetime.timedelta(seconds=240),
            duration=datetime.timedelta(seconds=120),
        )

        gaps = self.finder._find_gaps_in_items(start, end, [item])

        # Both 4-minute gaps should be filtered out (minimum is 5 minutes)
        assert len(gaps) == 0

    def test_overlapping_items_handled_correctly(self):
        """Test that overlapping items don't create negative gaps."""
        start = self.base
        end = self.base + datetime.timedelta(hours=3)

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

        gaps = self.finder._find_gaps_in_items(start, end, items)

        # Should have: gap before first item, then nothing (overlap), gap after second item
        assert len(gaps) == 2
        assert gaps[0].start == start
        assert gaps[0].end == items[0].starttime
        assert gaps[1].start == items[1].endtime()
        assert gaps[1].end == end

    def test_items_completely_outside_window(self):
        """Test that items before the window don't affect gap calculation."""
        start = self.base + datetime.timedelta(hours=2)
        end = self.base + datetime.timedelta(hours=3)

        # Item that ends exactly at window start
        item = MockScheduledItem(starttime=self.base, duration=datetime.timedelta(hours=2))

        gaps = self.finder._find_gaps_in_items(start, end, [item])

        # Item is skipped, entire window is a gap
        assert len(gaps) == 1
        assert gaps[0].start == start
        assert gaps[0].end == end

    def test_items_exactly_at_boundaries(self):
        """Test edge case where items start or end at window boundaries."""
        start = self.base
        end = self.base + datetime.timedelta(hours=2)

        # Item exactly fills the window
        item = MockScheduledItem(starttime=start, duration=datetime.timedelta(hours=2))

        gaps = self.finder._find_gaps_in_items(start, end, [item])

        # No gaps when item fills the window
        assert len(gaps) == 0

    def test_multiple_items_with_gaps(self):
        """Test realistic scenario with multiple scheduled items."""
        start = self.base
        end = self.base + datetime.timedelta(hours=4)

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

        gaps = self.finder._find_gaps_in_items(start, end, items)

        # Should have: gap at start, gap between first and second items, gap between second and third, gap at end
        assert len(gaps) == 4
