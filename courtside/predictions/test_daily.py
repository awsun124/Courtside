from copy import deepcopy
from datetime import datetime, timedelta, timezone
import tempfile
import unittest

from .daily import build_daily, save_daily

NOW = datetime(2026, 11, 15, 20, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=2)).isoformat()
CUTOFF = (NOW + timedelta(hours=1)).isoformat()


def fixtures():
    game = dict(game_id='espn:123', scheduled_start=START, home_team_id='espn:2', away_team_id='espn:13')
    event = dict(id='123', competitions=[dict(date=START,
        status=dict(type=dict(state='pre', name='STATUS_SCHEDULED')),
        competitors=[dict(homeAway='home', team=dict(id='2')),
                     dict(homeAway='away', team=dict(id='13'))])])
    book = dict(bookmaker='example', available_at=NOW.isoformat(), retrieved_at=NOW.isoformat(),
                home_market_probability=0.55, away_market_probability=0.45,
                home_decimal_odds=1.8, away_decimal_odds=2.2)
    matches = dict(generated_at=NOW.isoformat(), schedule_sources=[dict(retrieved_at=NOW.isoformat(),
        raw=dict(events=[event]))], matched=[dict(game, odds_game_id='odds:abc', bookmakers=[book])])
    forecasts = dict(model_version='test-v1', generated_at=NOW.isoformat(), predictions=[dict(game,
        generated_at=NOW.isoformat(), cutoff_at=CUTOFF, inputs_as_of=NOW.isoformat(), home_probability=0.6)])
    return matches, forecasts


class DailyTests(unittest.TestCase):
    def test_missing_model_is_explicit_and_market_remains_available(self):
        matches, _ = fixtures()
        row = build_daily(matches, '2026-11-15', NOW)['games'][0]
        self.assertEqual(row['model_status'], 'unavailable')
        self.assertIsNone(row['home_probability'])
        self.assertEqual(row['market_status'], 'available')
        self.assertIsNone(row['bookmakers'][0]['home_difference_percentage_points'])

    def test_valid_forecast_keeps_prices_and_separate_market_difference(self):
        matches, forecasts = fixtures()
        original = deepcopy(matches)
        row = build_daily(matches, '2026-11-15', NOW, forecasts)['games'][0]
        self.assertEqual(row['model_status'], 'available')
        self.assertAlmostEqual(row['away_probability'], 0.4)
        self.assertAlmostEqual(row['bookmakers'][0]['home_difference_percentage_points'], 5)
        self.assertEqual(row['bookmakers'][0]['home_decimal_odds'], 1.8)
        self.assertEqual(matches, original)

    def test_late_snapshot_cannot_claim_on_time_forecast(self):
        matches, forecasts = fixtures()
        row = build_daily(matches, '2026-11-15', NOW + timedelta(minutes=61), forecasts)['games'][0]
        self.assertEqual(row['model_status'], 'unavailable')
        self.assertEqual(row['market_status'], 'unavailable')

    def test_invalid_forecasts_are_rejected(self):
        for change in ('nan', 'bool', 'duplicate', 'team', 'tipoff', 'future_inputs', 'future_file'):
            matches, forecasts = fixtures()
            row = forecasts['predictions'][0]
            if change == 'nan': row['home_probability'] = float('nan')
            if change == 'bool': row['home_probability'] = True
            if change == 'duplicate': forecasts['predictions'].append(dict(row))
            if change == 'team': row['home_team_id'] = 'espn:13'
            if change == 'tipoff': row['scheduled_start'] = CUTOFF
            if change == 'future_inputs': row['inputs_as_of'] = CUTOFF
            if change == 'future_file': forecasts['generated_at'] = CUTOFF
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_daily(matches, '2026-11-15', NOW, forecasts)

    def test_stale_quotes_and_missing_markets(self):
        matches, forecasts = fixtures()
        matches['matched'][0]['bookmakers'][0]['available_at'] = (NOW - timedelta(hours=7)).isoformat()
        row = build_daily(matches, '2026-11-15', NOW, forecasts)['games'][0]
        self.assertEqual(row['market_status'], 'unavailable')
        self.assertTrue(row['bookmakers'][0]['stale'])
        matches['matched'] = []
        row = build_daily(matches, '2026-11-15', NOW, forecasts)['games'][0]
        self.assertEqual(row['model_status'], 'available')
        self.assertEqual(row['bookmakers'], [])

    def test_stale_reports_and_duplicate_schedule_fail(self):
        matches, _ = fixtures()
        with self.assertRaises(ValueError):
            build_daily(matches, '2026-11-15', NOW + timedelta(hours=7))
        matches['schedule_sources'] *= 2
        with self.assertRaises(ValueError):
            build_daily(matches, '2026-11-15', NOW)

    def test_date_scope_started_games_and_immutable_save(self):
        matches, _ = fixtures()
        self.assertEqual(build_daily(matches, '2026-11-16', NOW)['games'], [])
        self.assertEqual(build_daily(matches, '2026-11-15', NOW + timedelta(hours=2))['games'], [])
        report = build_daily(matches, '2026-11-15', NOW)
        with tempfile.TemporaryDirectory() as folder:
            path = save_daily(folder, report)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                save_daily(folder, report)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
