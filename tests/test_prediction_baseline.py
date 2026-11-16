from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import tempfile
import unittest

from courtside.predictions.baseline import fit_baseline, split_rows
from courtside.predictions.features import FEATURE_NAMES, FEATURE_VERSION
from scripts.collect_prediction_results import collect_results


def fixture():
    rows = []
    for index in range(90):
        cutoff = datetime(2024, 11, 1, 20, tzinfo=timezone.utc) + timedelta(days=index)
        target = int(index % 3 != 0)
        features = {key: 0 for key in FEATURE_NAMES}
        features['margin_difference'] = (index % 7) - 3
        rows.append(dict(game_id=str(index), cutoff_at=cutoff.isoformat(), target=target,
                         eligible=True, features=features))
    return dict(feature_version=FEATURE_VERSION, feature_names=list(FEATURE_NAMES), rows=rows,
                cutoff_minutes_before_tipoff=60, assumed_result_delay_hours=24)


def settings():
    return dict(train_before='2024-12-01', validation_before='2025-01-01', test_before='2025-02-01',
                minimum_partition_games=10, regularization_candidates=[0.01, 0.1], random_seed=42)


class BaselineSplitTests(unittest.TestCase):
    def test_partitions_are_disjoint_and_labels_obey_embargo(self):
        partitions, _ = split_rows(fixture(), settings())
        ids = [{r['game_id'] for r in rows} for rows in partitions.values()]
        self.assertFalse(ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
        self.assertTrue(all(r['cutoff_at'] < '2024-11-30' for r in partitions['train']))

    def test_invalid_features_duplicates_and_small_partitions_fail(self):
        original = fixture()
        duplicate = deepcopy(original)
        duplicate['rows'].append(duplicate['rows'][0])
        nonfinite = deepcopy(original)
        nonfinite['rows'][0]['features']['home_court'] = float('nan')
        for dataset in (duplicate, nonfinite, {**original, 'rows': original['rows'][:5]}):
            with self.assertRaises(ValueError):
                split_rows(dataset, settings())


@unittest.skipUnless(importlib.util.find_spec('sklearn'), 'Install requirements-ml.txt for model tests')
class BaselineFitTests(unittest.TestCase):
    def test_test_targets_cannot_change_selected_model_or_scaler(self):
        dataset = fixture()
        first, artifact = fit_baseline(dataset, settings())
        altered = deepcopy(dataset)
        for row in altered['rows']:
            if row['cutoff_at'] >= '2025-01-01':
                row['target'] = 1 - row['target']
        second, second_artifact = fit_baseline(altered, settings())
        self.assertEqual(artifact, second_artifact)
        self.assertEqual(first['selected_C'], second['selected_C'])
        self.assertEqual(first['partitions']['validation'], second['partitions']['validation'])
        self.assertEqual(len(first['test_brier_difference_95_interval']), 2)

    def test_scaler_is_fitted_on_training_only_and_artifact_predicts(self):
        import math
        dataset = fixture()
        partitions, _ = split_rows(dataset, settings())
        report, artifact = fit_baseline(dataset, settings())
        position = list(FEATURE_NAMES).index('margin_difference')
        expected = sum(r['features']['margin_difference'] for r in partitions['train']) / len(partitions['train'])
        self.assertAlmostEqual(artifact['mean'][position], expected)
        row = partitions['test'][0]
        logit = artifact['intercept'] + sum((row['features'][key] - mean) / scale * coefficient
            for key, mean, scale, coefficient in zip(FEATURE_NAMES, artifact['mean'], artifact['scale'], artifact['coefficients']))
        predicted = next(r for r in report['predictions'] if r['game_id'] == row['game_id'])
        self.assertAlmostEqual(1 / (1 + math.exp(-logit)), predicted['home_probability'])


class ResultsCollectorTests(unittest.TestCase):
    def test_regular_results_parse_without_player_data_and_cup_final_is_excluded(self):
        competition = dict(id='1', date='2024-10-22T23:00Z', status=dict(type=dict(completed=True)),
                           neutralSite=False, competitors=[
                           dict(homeAway='home', team=dict(id='1', displayName='Home'), score='110'),
                           dict(homeAway='away', team=dict(id='2', displayName='Away'), score='100')])
        event = dict(id='1', date='2024-10-22T23:00Z', season=dict(year=2025, type=2), competitions=[competition])
        with tempfile.TemporaryDirectory() as folder:
            report = collect_results('2024-10-22', '2024-10-22', folder,
                transport=lambda _: json.dumps(dict(events=[event])).encode(), sleep=lambda _: None)
            self.assertTrue(report['complete'])
            self.assertEqual(report['validated_games'], 1)
        event['competitions'][0]['notes'] = [dict(headline='Emirates NBA Cup - Championship')]
        with tempfile.TemporaryDirectory() as folder:
            report = collect_results('2024-10-22', '2024-10-22', folder,
                transport=lambda _: json.dumps(dict(events=[event])).encode(), sleep=lambda _: None)
            self.assertTrue(report['complete'])
            self.assertEqual(report['validated_games'], 0)
            self.assertEqual(len(report['days'][0]['excluded']), 1)
        event['competitions'][0]['notes'] = []
        event['competitions'][0]['status']['type'] = dict(completed=False, name='STATUS_POSTPONED')
        with tempfile.TemporaryDirectory() as folder:
            report = collect_results('2024-10-22', '2024-10-22', folder,
                transport=lambda _: json.dumps(dict(events=[event])).encode(), sleep=lambda _: None)
            self.assertTrue(report['complete'])
            self.assertEqual(report['validated_games'], 0)
            self.assertEqual(report['days'][0]['excluded'][0]['reason'], 'STATUS_POSTPONED')
        event['competitions'][0]['status']['type'] = dict(completed=True)
        event['competitions'][0]['type'] = dict(abbreviation='ALLSTAR')
        with tempfile.TemporaryDirectory() as folder:
            report = collect_results('2024-10-22', '2024-10-22', folder,
                transport=lambda _: json.dumps(dict(events=[event])).encode(), sleep=lambda _: None)
            self.assertTrue(report['complete'])
            self.assertEqual(report['validated_games'], 0)
            self.assertEqual(report['days'][0]['excluded'][0]['reason'], 'All-Star exhibition')


if __name__ == '__main__':
    unittest.main()
