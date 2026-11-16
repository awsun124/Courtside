from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from courtside.predictions.odds import ENDPOINT, collect_odds, collection_lock, fetch_odds, load_key, parse_events, read_latest

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
KEY = 'private-test-credential'


def event():
    return dict(id='game1', sport_key='basketball_nba', commence_time=(NOW + timedelta(hours=1)).isoformat(),
        home_team='Home', away_team='Away', bookmakers=[dict(key='book', last_update=NOW.isoformat(), markets=[
            dict(key='h2h', outcomes=[dict(name='Home', price=1.8), dict(name='Away', price=2.2)])])])


class OddsTests(unittest.TestCase):
    def test_key_loading_is_literal_and_environment_has_precedence(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            path = Path(folder) / '.env'
            path.write_text(f'OTHER=$(do-not-execute)\nODDS_API_KEY="{KEY}"\n')
            self.assertEqual(load_key(path), KEY)
            with patch.dict('os.environ', {'ODDS_API_KEY': 'environment-key'}):
                self.assertEqual(load_key(path), 'environment-key')
            path.write_text('ODDS_API_KEY=your_actual_key_here\n')
            with self.assertRaises(ValueError): load_key(path)

    def test_transport_keeps_key_out_of_command_and_reads_quota(self):
        def command(args, **kwargs):
            self.assertNotIn(KEY, str(args))
            self.assertEqual(kwargs['input'], f'x-api-key: {KEY}\n')
            self.assertEqual(args[-1], ENDPOINT)
            Path(args[args.index('--dump-header') + 1]).write_text(
                'HTTP/1.1 200 OK\nx-requests-remaining: 499\nx-requests-last: 1\n')
            return CompletedProcess(args, 0, '[]\n200', '')
        with patch('courtside.predictions.odds.subprocess.run', side_effect=command):
            payload, usage = fetch_odds(KEY)
        self.assertEqual(payload, [])
        self.assertEqual(usage['x-requests-remaining'], 499)

    def test_provider_error_cannot_echo_key(self):
        response = CompletedProcess([], 0, f'{{"error":"{KEY}"}}\n401', KEY)
        with patch('courtside.predictions.odds.subprocess.run', return_value=response):
            with self.assertRaises(ValueError) as caught: fetch_odds(KEY)
        self.assertNotIn(KEY, str(caught.exception))

    def test_market_probabilities_and_break_even_are_distinct(self):
        games, rejected, skipped = parse_events([event()], NOW)
        self.assertFalse(rejected or skipped)
        book = games[0]['bookmakers'][0]
        self.assertAlmostEqual(book['home_market_probability'] + book['away_market_probability'], 1)
        self.assertAlmostEqual(book['home_break_even_probability'], 1 / 1.8)
        self.assertNotEqual(book['home_market_probability'], book['home_break_even_probability'])

    def test_started_events_bad_prices_and_future_updates(self):
        started = event()
        started['commence_time'] = NOW.isoformat()
        games, _, skipped = parse_events([started], NOW)
        self.assertEqual(games, [])
        self.assertEqual(len(skipped), 1)
        for value in (1, float('nan'), True):
            bad = event()
            bad['bookmakers'][0]['markets'][0]['outcomes'][0]['price'] = value
            games, rejected, _ = parse_events([bad], NOW)
            self.assertEqual(games[0]['bookmakers'], [])
            self.assertEqual(len(rejected), 1)
        future = event()
        future['bookmakers'][0]['last_update'] = (NOW + timedelta(seconds=1)).isoformat()
        self.assertEqual(len(parse_events([future], NOW)[1]), 1)
        malformed = event()
        malformed['commence_time'] = 123
        self.assertEqual(len(parse_events([malformed], NOW)[1]), 1)

    def test_cache_reuse_filters_started_games_without_new_request(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'ODDS_API_KEY': KEY}):
            calls = []
            def transport(key):
                calls.append(key)
                return [event()], {'x-requests-remaining': 499}
            first, cached = collect_odds(folder, transport=transport, clock=lambda: NOW)
            self.assertFalse(cached)
            second, cached = collect_odds(folder, transport=transport, clock=lambda: NOW + timedelta(hours=2))
            self.assertTrue(cached)
            self.assertEqual(second['games'], [])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(read_latest(folder)['games']), 1)
            for path in Path(folder).rglob('*'):
                if path.is_file(): self.assertNotIn(KEY, path.read_text())

    def test_failed_refresh_preserves_snapshot_and_counts_attempt(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'ODDS_API_KEY': KEY}):
            collect_odds(folder, transport=lambda _: ([event()], {}), clock=lambda: NOW)
            pointer = (Path(folder) / 'latest.json').read_bytes()
            def fail(_): raise ValueError('Connection failed')
            with self.assertRaises(ValueError):
                collect_odds(folder, transport=fail, clock=lambda: NOW + timedelta(hours=7))
            self.assertEqual((Path(folder) / 'latest.json').read_bytes(), pointer)
            ledger = json.loads((Path(folder) / 'requests-2026-11.json').read_text())
            self.assertEqual(len(ledger['attempts']), 2)

    def test_budget_stops_before_network_and_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'requests-2026-11.json').write_text(json.dumps(dict(attempts=['attempt'] * 124, last_usage={})))
            with self.assertRaisesRegex(ValueError, 'budget'):
                collect_odds(folder, transport=lambda _: self.fail('Network should not run'), clock=lambda: NOW)
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'ODDS_API_KEY': KEY}):
            collect_odds(folder, transport=lambda _: ([event()], {}), clock=lambda: NOW)
            path = next((Path(folder) / 'snapshots').glob('*.json'))
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'checksum'): read_latest(folder)

    def test_credential_in_success_body_is_not_saved(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'ODDS_API_KEY': KEY}):
            with self.assertRaisesRegex(ValueError, 'credential'):
                collect_odds(folder, transport=lambda _: ([dict(error=KEY)], {}), clock=lambda: NOW)
            self.assertFalse((Path(folder) / 'latest.json').exists())
            for path in Path(folder).rglob('*'):
                if path.is_file(): self.assertNotIn(KEY, path.read_text())

    def test_quota_exhaustion_and_concurrent_collection_stop_before_request(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'requests-2026-11.json').write_text(json.dumps(
                dict(attempts=[], last_usage={'x-requests-remaining': 0})))
            with self.assertRaisesRegex(ValueError, 'budget'):
                collect_odds(folder, transport=lambda _: self.fail('No request expected'), clock=lambda: NOW)
            with collection_lock(Path(folder)):
                with self.assertRaisesRegex(ValueError, 'already running'):
                    collect_odds(folder, transport=lambda _: self.fail('No request expected'), clock=lambda: NOW)


if __name__ == '__main__':
    unittest.main()
