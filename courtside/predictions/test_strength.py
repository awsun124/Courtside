from copy import deepcopy
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from .elo import predict_elo
from .improve import fit, partition, predict
from .forecast_strength import forecast_games
from .research_report import compare_market
from .strength import build_strength_rows


def fixture_games(count=40):
    start = datetime(2024, 1, 1, 22, tzinfo=timezone.utc)
    return [dict(game_id=f'espn:{i}', season='2023-24', scheduled_start=(start + timedelta(days=i)).isoformat(),
        home=dict(team_id='espn:2', points=110 + i % 5),
        away=dict(team_id='espn:13', points=100 + (i % 3) * 8),
        home_win=110 + i % 5 > 100 + (i % 3) * 8, neutral_site=False) for i in range(count)]


class StrengthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.limit = threadpool_limits(limits=1)

    @classmethod
    def tearDownClass(cls):
        cls.limit.restore_original_limits()

    def test_current_and_future_outcomes_cannot_change_earlier_features(self):
        games = fixture_games()
        original = build_strength_rows(games)
        changed = deepcopy(games)
        for g in changed[20:]:
            g['home']['points'], g['away']['points'], g['home_win'] = 80, 150, False
        replay = build_strength_rows(changed)
        # At the next day's one-hour cutoff, the previous result is still delayed.
        for i in range(22):
            self.assertEqual(original[i]['features'], replay[i]['features'])
        self.assertNotEqual(original[22]['features'], replay[22]['features'])

    def test_order_invariance_and_existing_elo_equivalence_across_seasons(self):
        games = fixture_games()
        for i, game in enumerate(games[25:]):
            game['season'] = '2024-25'
            game['scheduled_start'] = (datetime(2024, 11, 1, 22, tzinfo=timezone.utc) + timedelta(days=i)).isoformat()
        rows = build_strength_rows(games)
        self.assertEqual(rows, build_strength_rows(list(reversed(games))))
        elo = predict_elo(games, dict(k=20, home_advantage=50, initial_rating=1500, rating_scale=400, season_retention=.75))
        for row in rows:
            self.assertAlmostEqual(row['elo_probability'], elo[row['game_id']]['home_probability'])
        self.assertFalse(rows[25]['eligible'])

    def test_as_of_prevents_later_available_results_entering_forecast(self):
        games = fixture_games()
        now = datetime(2024, 2, 9, 23, tzinfo=timezone.utc)
        upcoming = dict(game_id='espn:999', season='2023-24', scheduled_start=(now + timedelta(hours=20)).isoformat(),
                        home=dict(team_id='espn:2'), away=dict(team_id='espn:13'), neutral_site=False)
        first = build_strength_rows(games, [upcoming], as_of=now)[-1]
        changed = deepcopy(games)
        changed[-1]['home']['points'] = 200
        changed[-1]['home_win'] = True
        self.assertEqual(first, build_strength_rows(changed, [upcoming], as_of=now)[-1])
        self.assertNotIn('target', first)
        self.assertTrue(first['eligible'])

    def test_duplicate_and_invalid_teams_rejected(self):
        games = fixture_games()
        with self.assertRaises(ValueError):
            build_strength_rows(games + [games[0]])
        games[0]['home']['team_id'] = 'espn:9999'
        with self.assertRaises(ValueError):
            build_strength_rows(games)

    def test_exact_delay_boundary_and_warmup(self):
        games = fixture_games(8)
        games[-1]['scheduled_start'] = '2024-01-08T23:00:00+00:00'
        rows = build_strength_rows(games)
        self.assertEqual(rows[5]['history_counts']['home'], 4)
        self.assertFalse(rows[5]['eligible'])
        self.assertTrue(rows[6]['eligible'])
        self.assertEqual(rows[-1]['history_counts']['home'], 7)

    def test_training_embargo_and_reload(self):
        rows = build_strength_rows(fixture_games(70))
        train, validation = partition(rows, '2024-02-01', '2024-03-01')
        boundary = datetime(2024, 2, 1, 5, tzinfo=timezone.utc)
        self.assertTrue(all(datetime.fromisoformat(r['scheduled_start']) + timedelta(hours=24) <= boundary for r in train))
        self.assertTrue(set(r['game_id'] for r in train).isdisjoint(r['game_id'] for r in validation))
        spec = dict(kind='logistic', C=.1, columns=['elo_logit', 'srs_60', 'rest_difference'])
        model = fit(train, spec)
        original = predict(validation, spec, model)
        changed = deepcopy(validation)
        for row in changed:
            row['target'] = 1 - row['target']
            row['margin'] = 999
        np.testing.assert_array_equal(original, predict(changed, spec, model))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model.joblib'
            joblib.dump(model, path)
            np.testing.assert_array_equal(original, predict(validation, spec, joblib.load(path)))

    def test_inference_uses_real_fixture_without_result_and_respects_deadline(self):
        games = fixture_games()
        now = datetime(2024, 2, 10, 20, tzinfo=timezone.utc)
        fixture = dict(game_id='espn:999', scheduled_start=(now + timedelta(hours=2)).isoformat(),
                       home_team_id='espn:2', away_team_id='espn:13', neutral_site=False)
        spec = dict(kind='normal', columns=['margin_0.06'], sigma=15, name='test-model')
        selection = dict(last_training_label_available='2024-01-01T00:00:00+00:00', selected=dict(spec=spec))
        result = forecast_games(games, [fixture], selection, None, now)
        self.assertEqual(len(result['predictions']), 1)
        self.assertTrue(0 < result['predictions'][0]['home_probability'] < 1)
        fixture['scheduled_start'] = (now + timedelta(minutes=30)).isoformat()
        self.assertFalse(forecast_games(games, [fixture], selection, None, now)['predictions'])
        selection['last_training_label_available'] = '2024-03-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            forecast_games(games, [fixture], selection, None, now)

    def test_market_comparison_rejects_missing_mismatched_and_late_records(self):
        row = dict(game_id='1', cutoff_at='2026-01-01T20:00:00+00:00', target=1,
                   model_probability=.6, elo_probability=.55, reset_elo_probability=.55)
        old = dict(row, market_probability=.65, home_quote=dict(quoted_at='2026-01-01T19:50:00Z'),
                   away_quote=dict(quoted_at='2026-01-01T19:50:00Z'))
        with self.assertRaises(ValueError):
            compare_market([], dict(predictions=[old]))
        old['home_quote']['quoted_at'] = '2026-01-01T20:01:00Z'
        with self.assertRaises(ValueError):
            compare_market([row], dict(predictions=[old]))
        old['home_quote']['quoted_at'] = '2026-01-01T19:50:00Z'
        old['target'] = 0
        with self.assertRaises(ValueError):
            compare_market([row], dict(predictions=[old]))


if __name__ == '__main__':
    unittest.main()
