import datetime
from unittest.mock import Mock

from agenda.jukebox.dataclasses import WeighingResult
from agenda.jukebox.scoring.cooling_period import CoolingPeriod
from agenda.jukebox.scoring.freshness import FreshnessScorer
from agenda.jukebox.scoring.org_balance import OrganizationBalanceScorer


class TestCoolingPeriod:
    """Test suite for CoolingPeriod scorer."""

    def test_recent_video_gets_zero_score(self):
        """Videos that appear in recent schedule should get score of 0."""
        scorer = CoolingPeriod(weight=1.0, recent_count=10)

        # Create a video
        video = Mock()
        video.id = 42

        # Create scheduled items with our video in the recent list
        scheduled_items = []
        for i in range(15):
            item = Mock()
            item.video = Mock()
            item.video.id = i if i < 5 else 42 if i == 5 else i + 100
            scheduled_items.append(item)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, scheduled_items, now)

        assert result.criteria_name == "CoolingPeriod"
        assert result.score == 0.0

    def test_non_recent_video_gets_full_weight(self):
        """Videos not in recent schedule should get full weight."""
        scorer = CoolingPeriod(weight=2.5, recent_count=10)

        video = Mock()
        video.id = 999

        # Create scheduled items without our video
        scheduled_items = []
        for i in range(15):
            item = Mock()
            item.video = Mock()
            item.video.id = i
            scheduled_items.append(item)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, scheduled_items, now)

        assert result.criteria_name == "CoolingPeriod"
        assert result.score == 2.5

    def test_custom_recent_count(self):
        """Test that recent_count parameter works correctly."""
        scorer = CoolingPeriod(weight=1.0, recent_count=3)

        video = Mock()
        video.id = 10

        # Create 10 items, video appears at position 5 (index 4)
        scheduled_items = []
        for i in range(10):
            item = Mock()
            item.video = Mock()
            item.video.id = 10 if i == 4 else i
            scheduled_items.append(item)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, scheduled_items, now)

        # With recent_count=3, only last 3 items are checked (indexes 7, 8, 9)
        # Video at index 4 is NOT in recent, so should get full weight
        assert result.score == 1.0


class TestFreshnessScorer:
    """Test suite for FreshnessScorer."""

    def test_freshness_with_created_time(self):
        """Videos with recent created_time should score higher."""
        scorer = FreshnessScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.created_time = datetime.datetime(2024, 12, 1, 12, 0, 0)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, [], now)

        assert result.criteria_name == "FreshnessScorer"
        # Age is ~31 days, half-life is 30 days
        # freshness = 1 / (1 + age_seconds/half_life)
        age_seconds = (now - video.created_time).total_seconds()
        half_life = 30 * 24 * 3600
        expected_freshness = 1.0 / (1.0 + age_seconds / half_life)
        assert abs(result.score - expected_freshness) < 0.001

    def test_freshness_without_date_uses_id_proxy(self):
        """Videos without dates should use ID as a proxy."""
        scorer = FreshnessScorer(weight=1.0)

        video = Mock()
        video.id = 1234
        video.created_time = None

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, [], now)

        # Should use id % 1000 / 1000.0 as proxy
        expected = (1234 % 1000) / 1000.0
        assert result.score == expected

    def test_freshness_with_custom_weight(self):
        """Weight parameter should scale the score correctly."""
        scorer = FreshnessScorer(weight=3.5)

        video = Mock()
        video.id = 1
        video.created_time = datetime.datetime(2024, 12, 1, 12, 0, 0)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, [], now)

        age_seconds = (now - video.created_time).total_seconds()
        half_life = 30 * 24 * 3600
        base_freshness = 1.0 / (1.0 + age_seconds / half_life)
        expected = 3.5 * base_freshness
        assert abs(result.score - expected) < 0.001

    def test_very_old_video_has_low_score(self):
        """Very old videos should have scores approaching 0."""
        scorer = FreshnessScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.created_time = datetime.datetime(2020, 1, 1, 12, 0, 0)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, [], now)

        # 5 years old should have very low freshness
        assert result.score < 0.1

    def test_brand_new_video_has_high_score(self):
        """Brand new videos should score close to the weight."""
        scorer = FreshnessScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.created_time = datetime.datetime(2025, 1, 1, 11, 59, 0)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, [], now)

        # Just 1 minute old should score very high
        assert result.score > 0.99

    def test_future_video_handled_gracefully(self):
        """Videos with future dates should not cause negative scores."""
        scorer = FreshnessScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.created_time = datetime.datetime(2025, 1, 2, 12, 0, 0)

        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        result = scorer.score(video, [], now)

        # max() ensures age_seconds is at least 0
        assert result.score == 1.0


class TestOrganizationBalanceScorer:
    """Test suite for OrganizationBalanceScorer."""

    def test_org_with_no_airtime_gets_full_score(self):
        """Organizations with no scheduled airtime should get full weight."""
        scorer = OrganizationBalanceScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        # Schedule with different organizations
        scheduled_items = []
        for i in range(5):
            item = Mock()
            item.video = Mock()
            item.video.organization_id = 200  # Different org
            item.duration = datetime.timedelta(minutes=10)
            scheduled_items.append(item)

        result = scorer.score(video, scheduled_items, None)

        assert result.criteria_name == "OrganizationBalanceScorer"
        # 0 minutes airtime: score = 1.0 / (1.0 + 0) = 1.0
        assert result.score == 1.0

    def test_org_with_high_airtime_gets_low_score(self):
        """Organizations with lots of airtime should get penalized."""
        scorer = OrganizationBalanceScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        # Schedule with same organization
        scheduled_items = []
        for i in range(10):
            item = Mock()
            item.video = Mock()
            item.video.organization_id = 100
            item.duration = datetime.timedelta(minutes=10)
            scheduled_items.append(item)

        result = scorer.score(video, scheduled_items, None)

        # 100 minutes airtime: score = 1.0 / (1.0 + 100) = 1/101 ≈ 0.0099
        expected = 1.0 / (1.0 + 100.0)
        assert abs(result.score - expected) < 0.001

    def test_mixed_organizations(self):
        """Should correctly calculate airtime for mixed organizations."""
        scorer = OrganizationBalanceScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        scheduled_items = []
        # Add 3 videos from org 100 (30 minutes)
        for i in range(3):
            item = Mock()
            item.video = Mock()
            item.video.organization_id = 100
            item.duration = datetime.timedelta(minutes=10)
            scheduled_items.append(item)

        # Add 5 videos from org 200 (50 minutes)
        for i in range(5):
            item = Mock()
            item.video = Mock()
            item.video.organization_id = 200
            item.duration = datetime.timedelta(minutes=10)
            scheduled_items.append(item)

        result = scorer.score(video, scheduled_items, None)

        # Only 30 minutes for org 100
        expected = 1.0 / (1.0 + 30.0)
        assert abs(result.score - expected) < 0.001

    def test_video_without_organization_id_returns_zero(self):
        """Videos without organization_id should return score of 0."""
        scorer = OrganizationBalanceScorer(weight=1.0)

        video = Mock(spec=["id"])
        video.id = 1
        # No organization_id attribute

        scheduled_items = []
        result = scorer.score(video, scheduled_items, None)

        assert result.criteria_name == "OrganizationBalanceScorer"
        assert result.score == 0.0

    def test_custom_weight(self):
        """Weight parameter should scale the score correctly."""
        scorer = OrganizationBalanceScorer(weight=2.5)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        # No prior airtime
        scheduled_items = []
        result = scorer.score(video, scheduled_items, None)

        # 0 minutes: score = 2.5 * (1.0 / 1.0) = 2.5
        assert result.score == 2.5

    def test_varying_duration_videos(self):
        """Should handle videos of different durations correctly."""
        scorer = OrganizationBalanceScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        scheduled_items = []
        durations = [5, 10, 15, 20, 30]  # Total 80 minutes
        for duration in durations:
            item = Mock()
            item.video = Mock()
            item.video.organization_id = 100
            item.duration = datetime.timedelta(minutes=duration)
            scheduled_items.append(item)

        result = scorer.score(video, scheduled_items, None)

        expected = 1.0 / (1.0 + 80.0)
        assert abs(result.score - expected) < 0.001

    def test_schedule_items_with_none_videos(self):
        """Should handle schedule items with None videos gracefully."""
        scorer = OrganizationBalanceScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        scheduled_items = []
        # Add some valid items
        for i in range(3):
            item = Mock()
            item.video = Mock()
            item.video.organization_id = 100
            item.duration = datetime.timedelta(minutes=10)
            scheduled_items.append(item)

        # Add items with None videos
        for i in range(2):
            item = Mock()
            item.video = None
            item.duration = datetime.timedelta(minutes=10)
            scheduled_items.append(item)

        result = scorer.score(video, scheduled_items, None)

        # Should only count the 3 valid items (30 minutes)
        expected = 1.0 / (1.0 + 30.0)
        assert abs(result.score - expected) < 0.001

    def test_empty_schedule(self):
        """Empty schedule should give full weight to all organizations."""
        scorer = OrganizationBalanceScorer(weight=3.0)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        scheduled_items = []
        result = scorer.score(video, scheduled_items, None)

        assert result.score == 3.0

    def test_seconds_converted_to_minutes_correctly(self):
        """Duration in seconds should be converted to minutes properly."""
        scorer = OrganizationBalanceScorer(weight=1.0)

        video = Mock()
        video.id = 1
        video.organization_id = 100

        scheduled_items = []
        # Add one video with 90 seconds (1.5 minutes)
        item = Mock()
        item.video = Mock()
        item.video.organization_id = 100
        item.duration = datetime.timedelta(seconds=90)
        scheduled_items.append(item)

        result = scorer.score(video, scheduled_items, None)

        expected = 1.0 / (1.0 + 1.5)
        assert abs(result.score - expected) < 0.001


class TestScorerIntegration:
    """Integration tests for using multiple scorers together."""

    def test_all_scorers_return_weighing_result(self):
        """All scorers should return WeighingResult objects."""
        video = Mock()
        video.id = 1
        video.organization_id = 100
        video.created_time = datetime.datetime(2024, 12, 1)

        scheduled_items = []
        now = datetime.datetime(2025, 1, 1)

        scorers = [
            CoolingPeriod(),
            FreshnessScorer(),
            OrganizationBalanceScorer(),
        ]

        for scorer in scorers:
            result = scorer.score(video, scheduled_items, now)
            assert isinstance(result, WeighingResult)
            assert isinstance(result.criteria_name, str)
            assert isinstance(result.score, float)
            assert result.score >= 0.0

    def test_scorer_names_are_class_names(self):
        """Scorer criteria names should match their class names."""
        video = Mock()
        video.id = 1
        video.organization_id = 100
        video.created_time = datetime.datetime(2024, 12, 1)

        scheduled_items = []
        now = datetime.datetime(2025, 1, 1)

        cooling = CoolingPeriod()
        assert cooling.score(video, scheduled_items, now).criteria_name == "CoolingPeriod"

        freshness = FreshnessScorer()
        assert freshness.score(video, scheduled_items, now).criteria_name == "FreshnessScorer"

        org_balance = OrganizationBalanceScorer()
        assert (
            org_balance.score(video, scheduled_items, now).criteria_name
            == "OrganizationBalanceScorer"
        )
