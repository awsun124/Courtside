from datetime import datetime, timedelta, timezone
import json
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch

from .oddspapi_pilot import HistoricalRateLimit, cutoff_price, fetch_history

CUTOFF = datetime(2026, 11, 12, 23, tzinfo=timezone.utc)


class HistoricalPilotTests(unittest.TestCase):
    def payload(self, rows):
        return dict(bookmakers=dict(fanduel=dict(markets={'111': dict(outcomes={'111': dict(players={'0': rows})})})))

    def test_quotes_after_cutoff_cannot_leak_into_benchmark(self):
        payload = self.payload([dict(createdAt=(CUTOFF - timedelta(minutes=1)).isoformat(), price=2, active=True),
                                dict(createdAt=(CUTOFF + timedelta(seconds=1)).isoformat(), price=3, active=True)])
        self.assertEqual(cutoff_price(payload, 'fanduel', 111, CUTOFF)['price'], 2)

    def test_suspended_and_stale_quotes_are_unavailable(self):
        rows = [dict(createdAt=(CUTOFF - timedelta(minutes=2)).isoformat(), price=2, active=True),
                dict(createdAt=(CUTOFF - timedelta(minutes=1)).isoformat(), price=2, active=False)]
        self.assertIsNone(cutoff_price(self.payload(rows), 'fanduel', 111, CUTOFF))
        rows = [dict(createdAt=(CUTOFF - timedelta(hours=7)).isoformat(), price=2, active=True)]
        self.assertIsNone(cutoff_price(self.payload(rows), 'fanduel', 111, CUTOFF))

    def test_transport_keeps_credentials_out_of_args_and_redacts_errors(self):
        key = 'private-test-token'
        def command(args, **kwargs):
            self.assertNotIn(key, str(args))
            self.assertIn(key, kwargs['input'])
            return CompletedProcess(args, 0, key + '\n401', key)
        with patch('courtside.predictions.oddspapi_pilot.subprocess.run', side_effect=command):
            with self.assertRaises(ValueError) as caught:
                fetch_history(key, 'id123', 111, 'fanduel')
            self.assertNotIn(key, str(caught.exception))

    def test_not_found_is_missing_data_and_wrong_fixture_fails(self):
        response = CompletedProcess([], 0, '{}\n404', '')
        with patch('courtside.predictions.oddspapi_pilot.subprocess.run', return_value=response):
            self.assertIsNone(fetch_history('secret-key', 'id123', 111, 'fanduel'))
        response.stdout = json.dumps(dict(fixtureId='id456')) + '\n200'
        with patch('courtside.predictions.oddspapi_pilot.subprocess.run', return_value=response):
            with self.assertRaises(ValueError): fetch_history('secret-key', 'id123', 111, 'fanduel')

    def test_cooldown_is_distinct_from_monthly_allowance_exhaustion(self):
        response=CompletedProcess([],0,json.dumps(dict(retryMs=8000))+'\n429','')
        with patch('courtside.predictions.oddspapi_pilot.subprocess.run',return_value=response):
            with self.assertRaises(HistoricalRateLimit) as caught:
                fetch_history('secret-key','id123',None,'fanduel')
            self.assertEqual(caught.exception.seconds,8)
        response.stdout=json.dumps(dict(code='REQUEST_LIMIT_EXCEEDED'))+'\n429'
        with patch('courtside.predictions.oddspapi_pilot.subprocess.run',return_value=response):
            with self.assertRaisesRegex(ValueError,'monthly request allowance'):
                fetch_history('secret-key','id123',None,'fanduel')


if __name__ == '__main__':
    unittest.main()
