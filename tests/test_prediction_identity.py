from copy import deepcopy
import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('sklearn'), 'Install requirements-ml.txt for ablation metrics')
class IdentityComparisonTests(unittest.TestCase):
    def reports(self):
        settings = dict(seeds=[42, 43, 44], max_epochs=80)
        rows = [dict(game_id=str(i), fold='sample', cutoff_at=f'2024-01-{i+1:02}T20:00:00+00:00',
            target=i % 2, home_probability=0.6, elo_probability=0.6) for i in range(6)]
        first = dict(settings={**settings, 'use_identity': True}, source_hashes=dict(features='abc'), predictions=rows)
        second = deepcopy(first)
        second['settings']['use_identity'] = False
        return first, second

    def test_identical_predictions_have_zero_paired_difference(self):
        from courtside.predictions.identity_ablation import compare_identity
        first, second = self.reports()
        report = compare_identity(first, second)
        self.assertEqual(report['aggregate']['identity'], report['aggregate']['no_identity'])
        self.assertEqual(report['identity_minus_no_identity_brier_95_interval'], [0, 0])

    def test_improved_identity_predictions_have_negative_difference(self):
        from courtside.predictions.identity_ablation import compare_identity
        first, second = self.reports()
        for row in first['predictions']:
            row['home_probability'] = 0.9 if row['target'] else 0.1
        report = compare_identity(first, second)
        self.assertLess(report['aggregate']['identity']['brier'], report['aggregate']['no_identity']['brier'])
        self.assertLess(report['identity_minus_no_identity_brier_95_interval'][1], 0)

    def test_changed_settings_sources_or_test_games_fail(self):
        from courtside.predictions.identity_ablation import compare_identity
        for mutation in ('settings', 'source', 'game', 'target', 'cutoff', 'elo', 'duplicate'):
            first, second = self.reports()
            if mutation == 'settings': second['settings']['max_epochs'] = 100
            elif mutation == 'source': second['source_hashes']['features'] = 'different'
            elif mutation == 'game': second['predictions'].pop()
            elif mutation == 'target': second['predictions'][0]['target'] = 1
            elif mutation == 'cutoff': second['predictions'][0]['cutoff_at'] = '2024-01-01T21:00:00+00:00'
            elif mutation == 'elo': second['predictions'][0]['elo_probability'] = 0.5
            else: second['predictions'].append(second['predictions'][0])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compare_identity(first, second)


if __name__ == '__main__':
    unittest.main()
