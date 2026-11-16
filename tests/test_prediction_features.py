from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from courtside.predictions.features import build_features
from scripts.build_prediction_features import build_dataset


def game(key, start, home="a", away="b", home_points=110, away_points=100, season="2025-26"):
    return dict(game_id=key, scheduled_start=start, season=season,
                home=dict(team_id=home, points=home_points), away=dict(team_id=away, points=away_points),
                home_win=home_points > away_points, neutral_site=False)


class PredictionFeatureTests(unittest.TestCase):
    def test_current_and_future_results_cannot_change_current_features(self):
        games = [game("1", "2025-10-21T23:00Z"), game("2", "2025-10-24T23:00Z"),
                 game("3", "2025-10-27T23:00Z")]
        before = build_features(games, min_history=1)
        changed = deepcopy(games)
        changed[1].update(home_win=False)
        changed[1]["away"]["points"] = 120
        changed[2]["home"]["points"] = 150
        after = build_features(changed, min_history=1)
        self.assertEqual(before[1]["features"], after[1]["features"])
        self.assertEqual(before[1]["target"], 1)
        self.assertEqual(after[1]["target"], 0)
        self.assertEqual(before[:2], build_features(games[:2], min_history=1))

    def test_availability_delay_excludes_recent_result_but_rest_uses_schedule(self):
        rows = build_features([game("1", "2025-10-21T23:00Z"),
                               game("2", "2025-10-22T22:00Z")], min_history=1)
        second = rows[1]
        self.assertEqual(second["features"]["home_history_games"], 0)
        self.assertFalse(second["eligible"])
        self.assertEqual(second["features"]["home_back_to_back"], 1)
        self.assertEqual(second["rest_source_game_ids"]["home"], "1")
        self.assertEqual(second["history_game_ids"]["home"], [])

    def test_known_form_difference_and_warmup(self):
        rows = build_features([game("1", "2025-10-21T23:00Z", away="c"),
                               game("2", "2025-10-21T23:00Z", home="d", home_points=120),
                               game("3", "2025-10-24T23:00Z")], min_history=1)
        features = rows[-1]["features"]
        self.assertTrue(rows[-1]["eligible"])
        self.assertEqual(features["win_rate_difference"], 1)
        self.assertEqual(features["margin_difference"], 30)
        self.assertEqual(features["home_rest_days"], 2)
        self.assertEqual(rows[0]["features"]["home_rest_unknown"], 1)

    def test_input_order_does_not_change_features_and_seasons_reset(self):
        games = [game("1", "2024-10-21T23:00Z", season="2024-25"),
                 game("2", "2025-10-21T23:00Z")]
        rows = build_features(games, min_history=1)
        self.assertEqual(rows, build_features(list(reversed(games)), min_history=1))
        self.assertEqual(rows[1]["features"]["home_history_games"], 0)
        self.assertEqual(rows[1]["features"]["home_rest_unknown"], 1)

    def test_duplicate_games_and_inconsistent_results_are_rejected(self):
        one = game("1", "2025-10-21T23:00Z")
        with self.assertRaises(ValueError):
            build_features([one, one])
        with self.assertRaises(ValueError):
            build_features([{**one, "home_win": False}])

    def test_neutral_site_and_missing_site_indicator(self):
        one = game("1", "2025-10-21T23:00Z")
        for neutral, expected_unknown in ((True, 0), (None, 1)):
            row = build_features([{**one, "neutral_site": neutral}])[0]
            self.assertEqual(row["features"]["home_court"], 0)
            self.assertEqual(row["features"]["neutral_site_unknown"], expected_unknown)

    def test_feature_artifact_rejects_partial_dataset_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'history.json', Path(folder) / 'features.json'
            dataset = dict(coverage=dict(complete=False, validated_games=1),
                           games=[game("1", "2025-10-21T23:00Z")])
            source.write_text(json.dumps(dataset))
            with self.assertRaises(ValueError):
                build_dataset(source, output)
            dataset["coverage"]["complete"] = True
            source.write_text(json.dumps(dataset))
            report = build_dataset(source, output)
            self.assertEqual(report["eligible_games"], 0)
            self.assertEqual(len(report["source_sha256"]), 64)
            self.assertEqual(report["assumed_result_delay_hours"], 24)


if __name__ == '__main__':
    unittest.main()
