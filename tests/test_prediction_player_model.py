from copy import deepcopy
import importlib.util
import tempfile
from pathlib import Path
import unittest

from courtside.predictions.history import timestamp
from courtside.predictions.players import build_rotations
from courtside.predictions.features import FEATURE_NAMES
from tests.test_prediction_players import player_dataset


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Install requirements-ml.txt for neural model tests')
class PlayerModelTests(unittest.TestCase):
    def setUp(self):
        import torch
        torch.set_num_threads(1)
        self.settings = dict(embedding_dim=4, hidden_dim=8, player_dim=4, max_players=20,
            dropout=0.1, learning_rate=0.001, weight_decay=0.01, max_epochs=3, patience=2,
            batch_size=8, seeds=[42])
        dataset = player_dataset()
        snapshots = build_rotations(dataset, min_team_history=1)
        self.rotations = {r['game_id']: r for r in snapshots}
        rows = [dict(game_id=r['game_id'], cutoff_at=r['cutoff_at'], target=1,
            features={key: 0 for key in FEATURE_NAMES}) for r in snapshots[1:]]
        self.partitions = dict(train=rows[:3], validation=rows[3:5], test=rows[5:])
        self.elo = {r['game_id']: dict(home_probability=0.6) for r in rows}

    def test_train_only_vocabulary_and_scaling_with_unknown_players(self):
        from courtside.predictions.player_model import fit_preprocessing, tensorize
        original = fit_preprocessing(self.partitions['train'], self.rotations)
        altered = deepcopy(self.rotations)
        test_id = self.partitions['test'][0]['game_id']
        altered[test_id]['teams']['home']['players'][0]['player_id'] = 'new-player'
        altered[test_id]['teams']['home']['players'][0]['rates']['points_per36'] = 1000
        self.assertEqual(original, fit_preprocessing(self.partitions['train'], altered))
        tensors, _, counts = tensorize(self.partitions['test'], altered, original, self.elo, 20)
        self.assertEqual(tensors[0][0, 0, 0].item(), 0)
        self.assertEqual(counts['unknown'], 1)

    def test_order_and_zero_weight_padding_cannot_change_prediction(self):
        import torch
        from courtside.predictions.player_model import PlayerEncoder, fit_preprocessing, tensorize
        preprocessing = fit_preprocessing(self.partitions['train'], self.rotations)
        inputs, _, _ = tensorize(self.partitions['test'], self.rotations, preprocessing, self.elo, 20)
        model = PlayerEncoder(len(preprocessing['vocabulary']), self.settings).eval()
        with torch.no_grad():
            model.head[-1].weight.normal_()
            original = model(*inputs)
            permutation = torch.randperm(20)
            permuted = (inputs[0][:, :, permutation], inputs[1][:, :, permutation], inputs[2][:, :, permutation], *inputs[3:])
            self.assertTrue(torch.allclose(original, model(*permuted), atol=1e-6))
            padded = list(inputs)
            padded[1] = padded[1].clone()
            padded[1][:, :, 5:] = 100
            self.assertTrue(torch.allclose(original, model(*padded), atol=1e-6))

    def test_initial_network_is_exact_elo_anchor(self):
        import torch
        from courtside.predictions.player_model import PlayerEncoder, fit_preprocessing, tensorize
        preprocessing = fit_preprocessing(self.partitions['train'], self.rotations)
        inputs, _, _ = tensorize(self.partitions['test'], self.rotations, preprocessing, self.elo, 20)
        model = PlayerEncoder(len(preprocessing['vocabulary']), self.settings).eval()
        with torch.no_grad():
            self.assertAlmostEqual(torch.sigmoid(model(*inputs)).item(), 0.6, places=6)

    def test_test_targets_cannot_change_training_selection_or_weights(self):
        import torch
        from courtside.predictions.player_model import train_fold
        before, states, _ = train_fold(self.partitions, self.rotations, self.elo, self.settings)
        altered = deepcopy(self.partitions)
        altered['test'][0]['target'] = 0
        after, other_states, _ = train_fold(altered, self.rotations, self.elo, self.settings)
        self.assertEqual(before, after)
        for key in states[0]:
            self.assertTrue(torch.equal(states[0][key], other_states[0][key]))

    def test_checkpoint_reload_reproduces_predictions(self):
        import torch
        from courtside.predictions.player_model import PlayerEncoder, tensorize, train_fold
        report, states, predictions = train_fold(self.partitions, self.rotations, self.elo, self.settings)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'weights.pt'
            torch.save(states, path)
            restored = torch.load(path, weights_only=True)
        model = PlayerEncoder(len(report['preprocessing']['vocabulary']), self.settings).eval()
        model.load_state_dict(restored[0])
        inputs, _, _ = tensorize(self.partitions['test'], self.rotations, report['preprocessing'], self.elo, 20)
        with torch.no_grad():
            self.assertAlmostEqual(torch.sigmoid(model(*inputs)).item(), predictions['test'][0], places=6)


if __name__ == '__main__':
    unittest.main()
