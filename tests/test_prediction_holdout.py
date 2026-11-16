from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from courtside.predictions.holdout import evaluate_holdout, predict_checkpoint, verify_frozen


class FrozenArtifactTests(unittest.TestCase):
    def test_changed_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'weights.pt'
            path.write_bytes(b'frozen weights')
            plan = dict(frozen_files={'weights.pt': hashlib.sha256(path.read_bytes()).hexdigest()})
            verify_frozen(root, plan)
            path.write_bytes(b'changed weights')
            with self.assertRaisesRegex(ValueError, 'changed'):
                verify_frozen(root, plan)


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Install requirements-ml.txt for holdout inference tests')
class FrozenInferenceTests(unittest.TestCase):
    def setUp(self):
        import torch
        from courtside.predictions.features import build_features
        from courtside.predictions.players import build_rotations
        from courtside.predictions.player_model import PlayerEncoder, fit_preprocessing
        from tests.test_prediction_players import player_dataset
        torch.set_num_threads(1)
        data = player_dataset()
        self.history = dict(games=data['games'])
        rows = build_features(data['games'], min_history=1)
        self.features = dict(rows=rows, min_history=1, assumed_result_delay_hours=24, cutoff_minutes_before_tipoff=60)
        self.rotations_report = dict(rows=build_rotations(data, min_team_history=1), min_team_history=1,
            assumed_result_delay_hours=24, cutoff_minutes_before_tipoff=60)
        self.rotations = {r['game_id']: r for r in self.rotations_report['rows']}
        settings = dict(embedding_dim=4, hidden_dim=8, player_dim=4, max_players=20, dropout=0, seeds=[42])
        preprocessing = fit_preprocessing(rows[1:4], self.rotations)
        torch.manual_seed(42)
        model = PlayerEncoder(len(preprocessing['vocabulary']), settings)
        with torch.no_grad():
            model.head[-1].weight.normal_(std=0.01)
        self.checkpoint = dict(metadata=dict(settings=settings, preprocessing=preprocessing), states=[deepcopy(model.state_dict())])
        self.parameters = dict(initial_rating=1500, rating_scale=400, season_retention=0.75, k=20, home_advantage=50)
        self.plan = dict(test_start='2025-11-05', test_end_exclusive='2025-11-09', assumed_result_delay_hours=24,
                         cutoff_minutes_before_tipoff=60, minimum_history=1, bootstrap_seed=42)

    def test_window_is_respected_and_weights_are_unchanged(self):
        import torch
        before = deepcopy(self.checkpoint)
        report = evaluate_holdout(self.history, self.features, self.rotations_report,
                                  {'player': self.checkpoint}, self.parameters, self.plan)
        self.assertEqual(report['eligible_games'], 2)
        self.assertTrue(all(row['cutoff_at'] >= '2025-11-05' for row in report['predictions']))
        self.assertEqual(report['aggregate']['player']['games'], report['aggregate']['elo']['games'])
        self.assertEqual(before['metadata'], self.checkpoint['metadata'])
        for key, value in before['states'][0].items():
            self.assertTrue(torch.equal(value, self.checkpoint['states'][0][key]))

    def test_labels_cannot_affect_checkpoint_predictions(self):
        from courtside.predictions.elo import predict_elo
        rows = deepcopy(self.features['rows'][-2:])
        elo = predict_elo(self.history['games'], self.parameters)
        first, _ = predict_checkpoint(rows, self.rotations, elo, self.checkpoint['metadata'], self.checkpoint['states'])
        for row in rows:
            row['target'] = 1 - row['target']
        second, _ = predict_checkpoint(rows, self.rotations, elo, self.checkpoint['metadata'], self.checkpoint['states'])
        self.assertEqual(first, second)

    def test_policy_and_source_mismatches_fail(self):
        for mode in ('delay', 'target', 'missing_rotation'):
            features, rotations = deepcopy(self.features), deepcopy(self.rotations_report)
            if mode == 'delay': rotations['assumed_result_delay_hours'] = 12
            elif mode == 'target': features['rows'][-1]['target'] = 0
            else: rotations['rows'].pop()
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                evaluate_holdout(self.history, features, rotations, {'player': self.checkpoint}, self.parameters, self.plan)

    def test_missing_seed_checkpoint_fails(self):
        from courtside.predictions.elo import predict_elo
        with self.assertRaises(ValueError):
            predict_checkpoint(self.features['rows'][-2:], self.rotations,
                predict_elo(self.history['games'], self.parameters), self.checkpoint['metadata'], [])


if __name__ == '__main__':
    unittest.main()
