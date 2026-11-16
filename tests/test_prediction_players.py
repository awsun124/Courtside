from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from courtside.predictions.players import allocate_minutes, build_rotations, parse_player_game
from courtside.predictions.history import parse_result
from scripts.collect_prediction_players import collect_players
from tests.test_prediction_history import summary


def player_dataset():
    games, players, observations = [], [], []
    for index in range(7):
        payload = summary()
        key = f'{index:03}'
        payload['header']['id'] = key
        payload['header']['competitions'][0]['date'] = (
            datetime(2025, 10, 21, 23, tzinfo=timezone.utc) + timedelta(days=index * 3)).isoformat()
        game = parse_result(payload, key)
        logs, observed = parse_player_game(payload, game)
        games.append(game)
        players.extend(logs)
        observations.extend(observed)
    return dict(coverage=dict(selected_complete=True), games=games, players=players, observations=observations)


class PlayerParsingTests(unittest.TestCase):
    def test_zero_stat_row_with_unrecorded_minutes_is_explicitly_unknown(self):
        payload = summary()
        game = parse_result(payload, '001')
        athlete = dict(athlete=dict(id='999', displayName='Unknown minutes'), didNotPlay=False,
                       stats=['--', '0', '0-0', '0-0', '0-0', '0', '0', '0', '0', '0'])
        payload['boxscore']['players'][0]['statistics'][0]['athletes'].append(athlete)
        logs, observed = parse_player_game(payload, game)
        self.assertEqual(len(logs), 10)
        self.assertTrue(next(r for r in observed if r['player_id'] == 'espn:999')['participation_unknown'])
        athlete['stats'][1] = '1'
        with self.assertRaises(ValueError):
            parse_player_game(payload, game)

    def test_dnp_membership_is_retained_separately_from_logs(self):
        payload = summary()
        game = parse_result(payload, '001')
        payload['boxscore']['players'][0]['statistics'][0]['athletes'].append(
            dict(athlete=dict(id='999'), didNotPlay=True, stats=[]))
        logs, observed = parse_player_game(payload, game)
        self.assertEqual(len(logs), 10)
        self.assertEqual(len(observed), 11)
        self.assertEqual(next(r for r in observed if r['player_id'] == 'espn:999')['did_not_play'], True)
        game['home']['points'] += 1
        with self.assertRaisesRegex(ValueError, 'match'):
            parse_player_game(payload, game)


class RotationTests(unittest.TestCase):
    def test_capped_minutes_and_unknown_rotation(self):
        minutes = allocate_minutes({'a': 100, 'b': 10, 'c': 10, 'd': 10, 'e': 10, 'f': 10})
        self.assertAlmostEqual(sum(minutes.values()), 240)
        self.assertLessEqual(max(minutes.values()), 48)
        self.assertEqual(allocate_minutes({'a': 30, 'b': 20}), {})
        with self.assertRaises(ValueError):
            allocate_minutes({'a': float('nan')})

    def test_warmup_rates_and_minute_source_provenance(self):
        rows = build_rotations(player_dataset())
        self.assertFalse(rows[4]['eligible'])
        self.assertTrue(rows[5]['eligible'])
        team = rows[5]['teams']['home']
        self.assertEqual(team['history_games'], 5)
        self.assertAlmostEqual(sum(p['projected_minutes'] for p in team['players']), 240)
        self.assertEqual(len(team['minute_source_game_ids']), 5)
        self.assertEqual(team['players'][0]['history_games'], 5)
        self.assertAlmostEqual(team['players'][0]['rates']['points_per36'], 36 * 11 / 48)

    def test_current_and_future_logs_and_rosters_cannot_affect_current_projection(self):
        dataset = player_dataset()
        before = build_rotations(dataset)
        altered = deepcopy(dataset)
        key = dataset['games'][5]['game_id']
        for row in altered['players']:
            if row['game_id'] >= key:
                row['points'] += 20
                row['minutes'] = 20
            if row['game_id'] == key and row['player_id'] == 'espn:10':
                row['player_id'] = 'espn:999'
        for row in altered['observations']:
            if row['game_id'] == key and row['player_id'] == 'espn:10':
                row['player_id'] = 'espn:999'
        after = build_rotations(altered)
        self.assertEqual(before[5], after[5])
        self.assertNotEqual(before[6], after[6])

    def test_observed_new_team_removes_player_from_old_team(self):
        dataset = player_dataset()
        # In an earlier box score, swap two observed players between the teams.
        key = dataset['games'][4]['game_id']
        for collection in ('players', 'observations'):
            for row in dataset[collection]:
                if row['game_id'] == key and row['player_id'] in ('espn:10', 'espn:20'):
                    row['team_id'] = 'espn:2' if row['player_id'] == 'espn:10' else 'espn:1'
        rows = build_rotations(dataset)
        home_ids = {r['player_id'] for r in rows[5]['teams']['home']['players']}
        away_ids = {r['player_id'] for r in rows[5]['teams']['away']['players']}
        self.assertNotIn('espn:10', home_ids)
        self.assertIn('espn:10', away_ids)

    def test_missing_observations_partial_coverage_and_duplicate_logs_fail(self):
        dataset = player_dataset()
        for change in ('observations', 'coverage', 'duplicate'):
            altered = deepcopy(dataset)
            if change == 'observations':
                altered['observations'].pop()
            elif change == 'coverage':
                altered['coverage']['selected_complete'] = False
            else:
                altered['players'].append(altered['players'][0])
            with self.assertRaises(ValueError):
                build_rotations(altered)


class PlayerCollectorTests(unittest.TestCase):
    def test_prefix_scope_provenance_and_cache_reuse(self):
        payload = summary()
        game = parse_result(payload, '001')
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.json'
            source.write_text(json.dumps(dict(coverage=dict(complete=True), games=[game])))
            calls = []
            def transport(url):
                calls.append(url)
                return json.dumps(payload).encode()
            first = collect_players(source, Path(folder) / 'players', 1, transport, sleep=lambda _: None)
            second = collect_players(source, Path(folder) / 'players', 1, transport, sleep=lambda _: None)
            self.assertEqual(len(calls), 1)
            self.assertTrue(first['coverage']['selected_complete'])
            self.assertTrue(second['coverage']['full_history_complete'])
            self.assertEqual(len(first['coverage']['source_sha256']), 64)


if __name__ == '__main__':
    unittest.main()
