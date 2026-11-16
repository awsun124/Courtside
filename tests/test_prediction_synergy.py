from copy import deepcopy
import importlib.util
import tempfile
from pathlib import Path
import unittest

@unittest.skipUnless(importlib.util.find_spec('torch'), 'Install requirements-ml.txt for synergy tests')
class SynergyTests(unittest.TestCase):
    def setUp(self):
        # Reuse the small historical fixture and fold layout, not its test methods.
        from tests.test_prediction_player_model import PlayerModelTests
        fixture = PlayerModelTests()
        fixture.setUp()
        self.settings = {**fixture.settings, 'use_synergy': True, 'synergy_heads': 2}
        self.partitions, self.rotations, self.elo = fixture.partitions, fixture.rotations, fixture.elo

    def test_player_order_and_padding_do_not_affect_prediction(self):
        import torch
        from courtside.predictions.player_model import PlayerEncoder, fit_preprocessing, tensorize
        preprocessing = fit_preprocessing(self.partitions['train'], self.rotations)
        inputs, _, _ = tensorize(self.partitions['test'], self.rotations, preprocessing, self.elo, 20)
        model = PlayerEncoder(len(preprocessing['vocabulary']), self.settings).eval()
        with torch.no_grad():
            model.head[-1].weight.normal_()
            original = model(*inputs)
            order = torch.randperm(20)
            reordered = (inputs[0][:, :, order], inputs[1][:, :, order], inputs[2][:, :, order], *inputs[3:])
            self.assertTrue(torch.allclose(original, model(*reordered), atol=1e-6))
            padded = list(inputs)
            padded[1] = padded[1].clone()
            padded[1][:, :, 5:] = 1000
            self.assertTrue(torch.allclose(original, model(*padded), atol=1e-6))

    def test_teammate_changes_context_but_masked_players_do_not(self):
        import torch
        from courtside.predictions.player_model import SynergyLayer
        torch.manual_seed(42)
        layer = SynergyLayer(16, 2, 32, 0.1).eval()
        vectors = torch.randn(1, 6, 16)
        padding = torch.tensor([[False, False, False, False, True, True]])
        with torch.no_grad():
            before = layer(vectors, padding)
            changed = vectors.clone()
            changed[0, 1, 0] += 10
            self.assertFalse(torch.allclose(before[0, 0], layer(changed, padding)[0, 0]))
            changed = vectors.clone()
            changed[:, 4:] *= 1000
            self.assertTrue(torch.allclose(before[:, :4], layer(changed, padding)[:, :4], atol=1e-6))
            with self.assertRaises(ValueError):
                layer(vectors, torch.ones_like(padding))

    def test_initial_synergy_model_keeps_elo_anchor_and_validates_heads(self):
        import torch
        from courtside.predictions.player_model import PlayerEncoder, fit_preprocessing, tensorize, train_fold
        preprocessing = fit_preprocessing(self.partitions['train'], self.rotations)
        inputs, _, _ = tensorize(self.partitions['test'], self.rotations, preprocessing, self.elo, 20)
        model = PlayerEncoder(len(preprocessing['vocabulary']), self.settings).eval()
        with torch.no_grad():
            self.assertAlmostEqual(torch.sigmoid(model(*inputs)).item(), 0.6, places=6)
        with self.assertRaises(ValueError):
            train_fold(self.partitions, self.rotations, self.elo, {**self.settings, 'synergy_heads': 3})

    def test_test_targets_do_not_change_synergy_selection_and_checkpoint_reloads(self):
        import torch
        from courtside.predictions.player_model import PlayerEncoder, tensorize, train_fold
        report, states, predictions = train_fold(self.partitions, self.rotations, self.elo, self.settings)
        changed = deepcopy(self.partitions)
        changed['test'][0]['target'] = 0
        other, other_states, _ = train_fold(changed, self.rotations, self.elo, self.settings)
        self.assertEqual(report, other)
        for key in states[0]:
            self.assertTrue(torch.equal(states[0][key], other_states[0][key]))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'weights.pt'
            torch.save(states, path)
            loaded = torch.load(path, weights_only=True)
        model = PlayerEncoder(len(report['preprocessing']['vocabulary']), self.settings).eval()
        model.load_state_dict(loaded[0])
        inputs, _, _ = tensorize(self.partitions['test'], self.rotations, report['preprocessing'], self.elo, 20)
        with torch.no_grad():
            self.assertAlmostEqual(torch.sigmoid(model(*inputs)).item(), predictions['test'][0], places=6)


@unittest.skipUnless(importlib.util.find_spec('sklearn'), 'Install requirements-ml.txt for comparison tests')
class SynergyComparisonTests(unittest.TestCase):
    def reports(self):
        rows = [dict(game_id=str(i), fold='sample', cutoff_at=f'2024-01-{i+1:02}T20:00:00+00:00',
            target=i % 2, home_probability=0.6, elo_probability=0.6) for i in range(6)]
        baseline = dict(settings=dict(seeds=[42], learning_rate=0.001), source_hashes=dict(features='abc'), predictions=rows)
        synergy = deepcopy(baseline)
        synergy['settings'].update(use_synergy=True, synergy_heads=2, use_identity=True)
        return synergy, baseline

    def test_identical_scores_and_paired_direction(self):
        from courtside.predictions.synergy_comparison import compare_synergy
        synergy, baseline = self.reports()
        result = compare_synergy(synergy, baseline)
        self.assertEqual(result['synergy_minus_baseline_brier_95_interval'], [0, 0])
        for row in synergy['predictions']:
            row['home_probability'] = 0.9 if row['target'] else 0.1
        result = compare_synergy(synergy, baseline)
        self.assertLess(result['synergy_minus_baseline_brier_95_interval'][1], 0)

    def test_changed_inputs_settings_and_games_fail(self):
        from courtside.predictions.synergy_comparison import compare_synergy
        for mutation in ('source', 'optimizer', 'identity', 'games', 'labels', 'duplicate'):
            synergy, baseline = self.reports()
            if mutation == 'source': baseline['source_hashes']['features'] = 'other'
            elif mutation == 'optimizer': baseline['settings']['learning_rate'] = 1
            elif mutation == 'identity': baseline['settings']['use_identity'] = False
            elif mutation == 'games': baseline['predictions'].pop()
            elif mutation == 'labels': baseline['predictions'][0]['target'] = 1
            else: baseline['predictions'].append(baseline['predictions'][0])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compare_synergy(synergy, baseline)


if __name__ == '__main__':
    unittest.main()
