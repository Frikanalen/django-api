import datetime
import unittest
import pytest
from agenda.jukebox.picker import ProgramPicker
from agenda.jukebox.dataclasses import ScoredCandidate, WeighingResult
from agenda.jukebox.criteria import RANKING_CRITERIA
from agenda.jukebox.test_utils import make_mock_video
from portion import closedopen


# Patch render_candidates_table to a no-op for test
import agenda.jukebox.pprint


def fake_render_candidates_table(*a, **kw):
    pass


agenda.jukebox.pprint.render_candidates_table = fake_render_candidates_table


@pytest.mark.django_db
class TestProgramPicker(unittest.TestCase):
    def setUp(self):
        self.picker = ProgramPicker()
        self.now = datetime.datetime(2025, 1, 1, 12, 0)
        self.window = closedopen(
            datetime.datetime(2025, 1, 1, 12, 0),
            datetime.datetime(2025, 1, 1, 13, 0)
        )
        self.scheduled = []

    def test_score_candidate_returns_scoredcandidate(self):
        v = make_mock_video()
        result = self.picker._score_candidate(v, self.scheduled, self.now)
        self.assertIsInstance(result, ScoredCandidate)
        self.assertEqual(result.video, v)
        self.assertIsInstance(result.weights, list)
        self.assertTrue(all(isinstance(w, WeighingResult) for w in result.weights))

    def test_pick_returns_best_video(self):
        v1 = make_mock_video()
        v2 = make_mock_video()
        # Patch RANKING_CRITERIA so v2 always wins
        orig = RANKING_CRITERIA[:]

        class AlwaysWin:
            def score(self, video, scheduled, now):
                return WeighingResult(criteria_name="test", score=100 if video.id == v2.id else 0)

        from agenda.jukebox import criteria

        criteria.RANKING_CRITERIA[:] = [criteria.RankingCriterion("test", AlwaysWin())]
        try:
            # Patch render_candidates_table to a no-op to avoid TypeError
            import agenda.jukebox.pprint

            agenda.jukebox.pprint.render_candidates_table = lambda *a, **kw: None
            result = self.picker.pick(self.window, [v1, v2], self.scheduled, self.now)
            self.assertEqual(result, v2)
        finally:
            criteria.RANKING_CRITERIA[:] = orig


if __name__ == "__main__":
    unittest.main()
