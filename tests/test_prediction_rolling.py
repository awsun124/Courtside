from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from courtside.predictions.rolling import evaluate_rolling
from scripts.merge_prediction_results import merge_results
from tests.test_prediction_baseline import fixture, settings
from tests.test_prediction_features import game


class MergeResultsTests(unittest.TestCase):
    def source(self, folder, name, games, complete=True):
        path = Path(folder) / name
        path.write_text(json.dumps(dict(coverage=dict(complete=complete, validated_games=len(games)), games=games)))
        return path

    def test_merges_unique_games_and_records_source_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            one = self.source(folder, 'one.json', [game('1', '2024-10-21T23:00Z')])
            two = self.source(folder, 'two.json', [game('2', '2024-10-24T23:00Z')])
            coverage = merge_results([one, two], Path(folder) / 'merged.json')
            self.assertEqual(coverage['validated_games'], 2)
            self.assertEqual(len(coverage['sources'][0]['sha256']), 64)
            self.assertFalse(coverage['season_audits']['2025-26']['standard_82_game_season_counts_match'])
            with self.assertRaisesRegex(ValueError, 'Full-season audit'):
                merge_results([one, two], Path(folder) / 'full.json', full_seasons=True)

    def test_incomplete_sources_and_duplicate_ids_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            one = self.source(folder, 'one.json', [game('1', '2024-10-21T23:00Z')])
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                merge_results([one, one], Path(folder) / 'merged.json')
            partial = self.source(folder, 'partial.json', [], complete=False)
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                merge_results([partial], Path(folder) / 'merged.json')


@unittest.skipUnless(importlib.util.find_spec('sklearn'), 'Install requirements-ml.txt for rolling tests')
class RollingTests(unittest.TestCase):
    def options(self):
        return {**settings(), 'folds': [
            dict(name='early-january', train_before='2024-12-01', validation_before='2025-01-01', test_before='2025-01-15'),
            dict(name='late-january', train_before='2024-12-15', validation_before='2025-01-15', test_before='2025-02-01')]}

    def test_aggregate_uses_unique_test_predictions_and_fold_specific_baselines(self):
        report, models = evaluate_rolling(fixture(), self.options())
        self.assertEqual(len(models), 2)
        self.assertEqual(report['aggregate']['model']['games'], len(report['predictions']))
        self.assertEqual(len({r['game_id'] for r in report['predictions']}), len(report['predictions']))
        expected_brier = sum((r['baseline_probability'] - r['target']) ** 2 for r in report['predictions']) / len(report['predictions'])
        self.assertAlmostEqual(report['aggregate']['home_frequency']['brier'], expected_brier)

    def test_overlapping_test_windows_fail(self):
        options = self.options()
        options['folds'][1].update(validation_before='2025-01-01')
        with self.assertRaisesRegex(ValueError, 'overlap'):
            evaluate_rolling(fixture(), options)

    def test_later_outcomes_cannot_change_earlier_model(self):
        data = fixture()
        _, before = evaluate_rolling(data, self.options())
        changed = deepcopy(data)
        for row in changed['rows']:
            if row['cutoff_at'] >= '2025-01-15':
                row['target'] = 1 - row['target']
        _, after = evaluate_rolling(changed, self.options())
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
