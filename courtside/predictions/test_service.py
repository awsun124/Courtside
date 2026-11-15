from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from courtside.api import create_app
from .daily import build_daily, save_daily
from .service import read_predictions
from .test_daily import fixtures, NOW


class PredictionReadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.matches, self.forecasts = fixtures()
        self.report = build_daily(self.matches, '2026-11-15', NOW, self.forecasts)

    def test_missing_date_does_not_create_files(self):
        result = read_predictions(self.folder.name, '2026-11-15', NOW)
        self.assertEqual(result['status'], 'no_snapshot')
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    def test_projection_does_not_expose_internal_sources_and_keeps_historical_difference(self):
        self.report['sources'] = dict(internal_secret='not-public')
        path = save_daily(self.folder.name, self.report)
        before = path.read_bytes()
        result = read_predictions(self.folder.name, '2026-11-15', NOW + timedelta(hours=8))
        game = result['games'][0]
        self.assertEqual(game['home_team'], 'Boston Celtics')
        self.assertEqual(game['home_probability'], 0.6)
        self.assertTrue(game['tipoff_passed'])
        self.assertTrue(game['bookmakers'][0]['stale'])
        self.assertFalse(game['bookmakers'][0]['current'])
        self.assertAlmostEqual(game['bookmakers'][0]['home_difference_percentage_points'], 5)
        self.assertNotIn('not-public', json.dumps(result))
        self.assertEqual(before, path.read_bytes())

    def test_later_runs_do_not_replace_first_forecast_or_erase_started_games(self):
        save_daily(self.folder.name, self.report)
        self.forecasts['predictions'][0]['home_probability'] = 0.8
        later = build_daily(self.matches, '2026-11-15', NOW + timedelta(minutes=1), self.forecasts)
        save_daily(self.folder.name, later)
        later['saved_at'] = (NOW + timedelta(hours=3)).isoformat()
        later['games'] = []
        save_daily(self.folder.name, later)
        result = read_predictions(self.folder.name, '2026-11-15', NOW + timedelta(hours=4))
        self.assertEqual(len(result['games']), 1)
        self.assertEqual(result['games'][0]['home_probability'], 0.6)

    def test_real_forecast_replaces_prior_unavailable_state(self):
        empty = build_daily(self.matches, '2026-11-15', NOW)
        save_daily(self.folder.name, empty)
        self.report['saved_at'] = (NOW + timedelta(minutes=1)).isoformat()
        save_daily(self.folder.name, self.report)
        result = read_predictions(self.folder.name, '2026-11-15', NOW + timedelta(minutes=2))
        self.assertEqual(result['games'][0]['model_status'], 'available')

    def test_corrupt_snapshot_is_not_silently_hidden(self):
        path = save_daily(self.folder.name, self.report)
        path.write_text('{')
        with self.assertRaises(ValueError): read_predictions(self.folder.name, '2026-11-15', NOW)


class PredictionApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_route_date_validation_and_safe_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(predictions_folder=folder, scheduled=False)
            app.state.news = SimpleNamespace(clock=lambda: NOW)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                with patch('courtside.predictions.odds.fetch_odds', side_effect=AssertionError('No odds calls')):
                    result = await client.get('/api/predictions')
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()['date'], '2026-11-15')
                    self.assertEqual(result.json()['status'], 'no_snapshot')
                    for day in ('bad', '../../.env', '2026-02-30'):
                        self.assertEqual((await client.get('/api/predictions', params={'date': day})).status_code, 422)
                    self.assertEqual((await client.post('/api/predictions')).status_code, 405)
                    matches, forecasts = fixtures()
                    path = save_daily(folder, build_daily(matches, '2026-11-15', NOW, forecasts))
                    self.assertEqual((await client.get('/api/predictions')).json()['games'][0]['home_probability'], 0.6)
                    path.write_text('private-invalid-file')
                    error = await client.get('/api/predictions')
                    self.assertEqual(error.status_code, 503)
                    self.assertNotIn(folder, error.text)
                    self.assertNotIn('private-invalid-file', error.text)


if __name__ == '__main__':
    unittest.main()
