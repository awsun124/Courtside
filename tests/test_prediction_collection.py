from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.collect_prediction_history import collect_range
from tests.test_prediction_history import summary


class PredictionCollectionTests(unittest.TestCase):
    def collect(self, start, end, folder, transport):
        with redirect_stdout(io.StringIO()):
            return collect_range(start, end, folder, transport, sleep=lambda _: None)

    def test_empty_days_are_valid_and_second_run_reuses_raw_data(self):
        calls = []
        def transport(url):
            calls.append(url)
            if "scoreboard?dates=20251021" in url:
                return json.dumps(dict(events=[dict(id="001", date="2025-10-22T02:00Z")])).encode()
            if "scoreboard?" in url:
                return b'{"events": []}'
            return json.dumps(summary()).encode()
        with tempfile.TemporaryDirectory() as folder:
            coverage = self.collect("2025-10-21", "2025-10-22", folder, transport)
            self.assertTrue(coverage["complete"])
            self.assertEqual(coverage["validated_games"], 1)
            self.assertEqual(coverage["player_rows"], 10)
            self.collect("2025-10-21", "2025-10-22", folder, transport)
            self.assertEqual(len(calls), 3)
            saved = json.loads((Path(folder) / 'history-2025-10-21-2025-10-22.json').read_text())
            self.assertEqual(len(saved["games"]), 1)

    def test_failed_day_does_not_discard_successful_days_or_claim_completeness(self):
        def transport(url):
            if "20251021" in url:
                raise ValueError("Temporary source failure")
            return b'{"events": []}'
        with tempfile.TemporaryDirectory() as folder:
            coverage = self.collect("2025-10-21", "2025-10-22", folder, transport)
            self.assertFalse(coverage["complete"])
            self.assertIsNone(coverage["days"][0]["listed_games"])
            self.assertTrue(coverage["days"][1]["complete"])
            recovered = self.collect("2025-10-21", "2025-10-22", folder,
                                     lambda url: b'{"events": []}')
            self.assertTrue(recovered["complete"])

    def test_duplicate_game_across_dates_marks_dataset_incomplete(self):
        report = dict(rejected=[], games=[dict(game_id="espn:001", scheduled_start="2025-10-22T02:00Z")],
                      players=[], validated_games=1, scoreboard_games=1)
        with tempfile.TemporaryDirectory() as folder, patch('scripts.collect_prediction_history.collect', return_value=report):
            coverage = self.collect("2025-10-21", "2025-10-22", folder, None)
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["validated_games"], 1)

    def test_invalid_ranges_do_not_fetch(self):
        for start, end in (("2025-10-22", "2025-10-21"), ("2024-01-01", "2025-01-01")):
            with self.assertRaises(ValueError):
                self.collect(start, end, Path('/tmp/unused-prediction-test'), None)


if __name__ == '__main__':
    unittest.main()
