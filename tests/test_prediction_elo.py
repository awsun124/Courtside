from copy import deepcopy
from datetime import timedelta
import importlib.util
import unittest

from courtside.predictions.elo import compare_elo, predict_elo
from courtside.predictions.history import timestamp
from courtside.predictions.rolling import evaluate_rolling
from tests.test_prediction_baseline import fixture, settings
from tests.test_prediction_features import game


PARAMETERS = dict(initial_rating=1500, rating_scale=400, season_retention=0.75, k=20, home_advantage=75)


class EloPredictionTests(unittest.TestCase):
    def test_equal_neutral_teams_and_home_advantage(self):
        first = game('1', '2023-10-24T23:00Z')
        self.assertGreater(predict_elo([first], PARAMETERS)['1']['home_probability'], 0.5)
        first['neutral_site'] = True
        self.assertEqual(predict_elo([first], PARAMETERS)['1']['home_probability'], 0.5)

    def test_delayed_update_and_conservation(self):
        games = [game('1', '2023-10-24T23:00Z'), game('2', '2023-10-25T22:00Z'),
                 game('3', '2023-10-26T00:00Z')]
        predictions = predict_elo(games, PARAMETERS)
        self.assertEqual(predictions['2']['home_rating'], 1500)
        self.assertGreater(predictions['3']['home_rating'], 1500)
        self.assertAlmostEqual(predictions['3']['home_rating'] + predictions['3']['away_rating'], 3000)

    def test_current_and_future_results_do_not_change_current_prediction(self):
        games = [game('1', '2023-10-24T23:00Z'), game('2', '2023-10-27T23:00Z'),
                 game('3', '2023-10-30T23:00Z')]
        before = predict_elo(games, PARAMETERS)
        altered = deepcopy(games)
        altered[1]['away']['points'] = 120
        altered[1]['home_win'] = False
        altered[2]['home']['points'] = 140
        after = predict_elo(altered, PARAMETERS)
        self.assertEqual(before['2'], after['2'])
        self.assertNotEqual(before['3']['home_probability'], after['3']['home_probability'])

    def test_offseason_regression_and_input_order(self):
        first = game('1', '2022-10-24T23:00Z', season='2022-23')
        first['neutral_site'] = True
        second = game('2', '2023-10-24T23:00Z', season='2023-24')
        predictions = predict_elo([first, second], PARAMETERS)
        self.assertEqual(predictions['2']['home_rating'], 1507.5)
        self.assertEqual(predictions, predict_elo([second, first], PARAMETERS))

    def test_invalid_parameters_fail(self):
        for value in (0, float('nan')):
            with self.assertRaises(ValueError):
                predict_elo([], {**PARAMETERS, 'rating_scale': value})


@unittest.skipUnless(importlib.util.find_spec('sklearn'), 'Install requirements-ml.txt for benchmark tests')
class EloComparisonTests(unittest.TestCase):
    def data(self):
        features = fixture()
        games = []
        for row in features['rows']:
            start = (timestamp(row['cutoff_at']) + timedelta(hours=1)).isoformat()
            games.append(game(row['game_id'], start, home_points=110 if row['target'] else 90, season='2024-25'))
        rolling_settings = {**settings(), 'folds': [dict(name='january', **{
            key: settings()[key] for key in ('train_before', 'validation_before', 'test_before')})]}
        elo_settings = dict(initial_rating=1500, rating_scale=400, season_retention=0.75,
                            k_candidates=[10, 20], home_advantage_candidates=[50, 100])
        return games, features, rolling_settings, elo_settings

    def test_same_games_and_validation_only_selection(self):
        games, features, options, parameters = self.data()
        rolling, _ = evaluate_rolling(features, options)
        report = compare_elo(games, features, rolling, parameters)
        self.assertEqual(report['aggregate']['elo']['games'], rolling['aggregate']['model']['games'])
        for row, g in zip(features['rows'], games):
            if row['cutoff_at'] >= '2025-01-01':
                row['target'] = 1 - row['target']
                g['home_win'] = bool(row['target'])
                g['home']['points'] = 110 if row['target'] else 90
        second_rolling, _ = evaluate_rolling(features, options)
        second = compare_elo(games, features, second_rolling, parameters)
        self.assertEqual(report['folds'][0]['selected_parameters'], second['folds'][0]['selected_parameters'])
        self.assertEqual(report['folds'][0]['validation_trials'], second['folds'][0]['validation_trials'])

    def test_mismatched_sources_fail(self):
        games, features, options, parameters = self.data()
        rolling, _ = evaluate_rolling(features, options)
        with self.assertRaisesRegex(ValueError, 'match'):
            compare_elo(games[:-1], features, rolling, parameters)


if __name__ == '__main__':
    unittest.main()
