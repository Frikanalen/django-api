import unittest
import datetime
from portion import closedopen
from agenda.jukebox.gapfinder import ScheduleGapFinder


class TestScheduleGapFinder(unittest.TestCase):
    def setUp(self):
        self.finder = ScheduleGapFinder()

    def test_no_gaps_fully_occupied(self):
        """Test when window is completely filled with a reserved area."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 13, 0)
        )
        reserved_areas = [closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 13, 0)
        )]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas)
        self.assertEqual(gaps, [])

    def test_empty_reserved_areas(self):
        """Test when there are no reserved areas - entire window should be a gap."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 13, 0)
        )
        reserved_areas = []
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=0)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 13, 0))

    def test_single_gap_in_middle(self):
        """Test when there's a gap in the middle with reserved areas on both sides."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [closedopen(
            datetime.datetime(2025, 1, 1, 12, 10), datetime.datetime(2025, 1, 1, 12, 20)
        )]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 10))
        self.assertEqual(gaps[1].lower, datetime.datetime(2025, 1, 1, 12, 20))
        self.assertEqual(gaps[1].upper, datetime.datetime(2025, 1, 1, 12, 30))

    def test_gap_at_start_only(self):
        """Test when there's only a gap at the start of the window."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [closedopen(
            datetime.datetime(2025, 1, 1, 12, 10), datetime.datetime(2025, 1, 1, 12, 30)
        )]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 10))

    def test_gap_at_end_only(self):
        """Test when there's only a gap at the end of the window."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 20)
        )]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 20))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 30))

    def test_gap_filtering_by_minimum_size(self):
        """Test that gaps smaller than minimum_gap_seconds are filtered out."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [closedopen(
            datetime.datetime(2025, 1, 1, 12, 10), datetime.datetime(2025, 1, 1, 12, 20)
        )]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=700)
        self.assertEqual(len(gaps), 0)

    def test_multiple_gaps_with_complete_verification(self):
        """Test multiple gaps with full verification of all bounds."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 12, 5), datetime.datetime(2025, 1, 1, 12, 10)),
            closedopen(datetime.datetime(2025, 1, 1, 12, 15), datetime.datetime(2025, 1, 1, 12, 20)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 3)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 5))
        self.assertEqual(gaps[1].lower, datetime.datetime(2025, 1, 1, 12, 10))
        self.assertEqual(gaps[1].upper, datetime.datetime(2025, 1, 1, 12, 15))
        self.assertEqual(gaps[2].lower, datetime.datetime(2025, 1, 1, 12, 20))
        self.assertEqual(gaps[2].upper, datetime.datetime(2025, 1, 1, 12, 30))

    def test_overlapping_reserved_areas(self):
        """Test that overlapping reserved areas are handled correctly."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 12, 5), datetime.datetime(2025, 1, 1, 12, 15)),
            closedopen(datetime.datetime(2025, 1, 1, 12, 10), datetime.datetime(2025, 1, 1, 12, 20)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 5))
        self.assertEqual(gaps[1].lower, datetime.datetime(2025, 1, 1, 12, 20))
        self.assertEqual(gaps[1].upper, datetime.datetime(2025, 1, 1, 12, 30))

    def test_adjacent_reserved_areas(self):
        """Test that adjacent (touching) reserved areas leave no gap between them."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 12, 5), datetime.datetime(2025, 1, 1, 12, 10)),
            closedopen(datetime.datetime(2025, 1, 1, 12, 10), datetime.datetime(2025, 1, 1, 12, 15)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 5))
        self.assertEqual(gaps[1].lower, datetime.datetime(2025, 1, 1, 12, 15))
        self.assertEqual(gaps[1].upper, datetime.datetime(2025, 1, 1, 12, 30))

    def test_reserved_area_outside_window(self):
        """Test that reserved areas outside the window don't affect gaps."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 11, 0), datetime.datetime(2025, 1, 1, 11, 30)),
            closedopen(datetime.datetime(2025, 1, 1, 13, 0), datetime.datetime(2025, 1, 1, 13, 30)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 30))

    def test_reserved_area_partially_overlapping_window(self):
        """Test that reserved areas partially overlapping the window are cropped correctly."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 11, 50), datetime.datetime(2025, 1, 1, 12, 10)),
            closedopen(datetime.datetime(2025, 1, 1, 12, 20), datetime.datetime(2025, 1, 1, 12, 40)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 10))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 20))

    def test_reserved_area_spanning_entire_window(self):
        """Test that a reserved area larger than the window results in no gaps."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 11, 0), datetime.datetime(2025, 1, 1, 13, 0)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas)
        self.assertEqual(len(gaps), 0)

    def test_unsorted_reserved_areas(self):
        """Test that unsorted reserved areas are handled correctly."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 12, 20), datetime.datetime(2025, 1, 1, 12, 25)),
            closedopen(datetime.datetime(2025, 1, 1, 12, 5), datetime.datetime(2025, 1, 1, 12, 10)),
            closedopen(datetime.datetime(2025, 1, 1, 12, 15), datetime.datetime(2025, 1, 1, 12, 18)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=60)
        # Gaps: 12:00-12:05, 12:10-12:15, 12:18-12:20, 12:25-12:30
        self.assertEqual(len(gaps), 4)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 5))
        self.assertEqual(gaps[1].lower, datetime.datetime(2025, 1, 1, 12, 10))
        self.assertEqual(gaps[1].upper, datetime.datetime(2025, 1, 1, 12, 15))
        self.assertEqual(gaps[2].lower, datetime.datetime(2025, 1, 1, 12, 18))
        self.assertEqual(gaps[2].upper, datetime.datetime(2025, 1, 1, 12, 20))
        self.assertEqual(gaps[3].lower, datetime.datetime(2025, 1, 1, 12, 25))
        self.assertEqual(gaps[3].upper, datetime.datetime(2025, 1, 1, 12, 30))

    def test_minimum_gap_seconds_zero(self):
        """Test that minimum_gap_seconds=0 returns all gaps regardless of size."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0, 0), datetime.datetime(2025, 1, 1, 12, 0, 10)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 12, 0, 3), datetime.datetime(2025, 1, 1, 12, 0, 7)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas, minimum_gap_seconds=0)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 0, 3))
        self.assertEqual(gaps[1].lower, datetime.datetime(2025, 1, 1, 12, 0, 7))
        self.assertEqual(gaps[1].upper, datetime.datetime(2025, 1, 1, 12, 0, 10))

    def test_default_minimum_gap_seconds(self):
        """Test the default minimum_gap_seconds value (300 seconds = 5 minutes)."""
        window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0), datetime.datetime(2025, 1, 1, 12, 30)
        )
        reserved_areas = [
            closedopen(datetime.datetime(2025, 1, 1, 12, 10), datetime.datetime(2025, 1, 1, 12, 20)),
        ]
        gaps = self.finder.find_schedule_gaps(window, reserved_areas)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0].lower, datetime.datetime(2025, 1, 1, 12, 0))
        self.assertEqual(gaps[0].upper, datetime.datetime(2025, 1, 1, 12, 10))
        self.assertEqual(gaps[1].lower, datetime.datetime(2025, 1, 1, 12, 20))
        self.assertEqual(gaps[1].upper, datetime.datetime(2025, 1, 1, 12, 30))


if __name__ == "__main__":
    unittest.main()
