from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from courtside.predictions.history import parse_result
from courtside.predictions.player_bulk import ALIASES, cache_csv, import_bulk, parse_bulk_game, BASE_URL
from courtside.predictions.players import PLAYER_STATS, parse_player_game
from tests.test_prediction_history import summary


class PlayerBulkTests(unittest.TestCase):
    def data(self):
        payload = summary()
        game = parse_result(payload, '001')
        logs, _ = parse_player_game(payload, game)
        rows = []
        for player in logs:
            side = 'home' if player['team_id'] == game['home']['team_id'] else 'away'
            row = dict(game_id='001', athlete_id=player['player_id'][5:], team_id=player['team_id'][5:],
                home_away=side, team_score=str(game[side]['points']), game_date_time=game['scheduled_start'],
                did_not_play='false', minutes=str(player['minutes']), athlete_display_name=player['name'])
            row.update({ALIASES.get(key, key): str(player[key]) for key in PLAYER_STATS})
            rows.append(row)
        return game, rows

    def test_numeric_logs_and_dnp_observations(self):
        game, rows = self.data()
        dnp = {**rows[0], 'athlete_id': '999', 'did_not_play': 'true', 'minutes': ''}
        logs, observations = parse_bulk_game(rows + [dnp], game)
        self.assertEqual(len(logs), 10)
        self.assertEqual(len(observations), 11)
        self.assertTrue(observations[-1]['did_not_play'])

    def test_duplicate_mismatch_and_invalid_shooting_fail(self):
        game, rows = self.data()
        with self.assertRaises(ValueError):
            parse_bulk_game(rows + [rows[0]], game)
        for column, value in (('team_score', '999'), ('field_goals_made', '100'), ('game_date_time', '2020-01-01T00:00Z')):
            altered = deepcopy(rows)
            altered[0][column] = value
            with self.assertRaises(ValueError):
                parse_bulk_game(altered, game)

    def test_missing_games_cannot_claim_complete_import(self):
        game, rows = self.data()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.csv'
            with path.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            result = import_bulk(dict(coverage=dict(complete=True), games=[game]), [path], [])
            self.assertTrue(result['coverage']['full_history_complete'])
            empty = import_bulk(dict(coverage=dict(complete=True), games=[game]), [], [])
            self.assertFalse(empty['coverage']['selected_complete'])
            self.assertEqual(len(empty['coverage']['rejected']), 1)


if __name__ == '__main__':
    unittest.main()
