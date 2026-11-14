from copy import deepcopy
import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
import tempfile
import unittest
from unittest.mock import patch

from courtside.predictions.history import fetch_bytes, game_ids, parse_game, read_source
from scripts.collect_prediction_sample import collect


def summary():
    groups = []
    for team in ("1", "2"):
        athletes = []
        for index in range(5):
            extra = team == "1" and index == 0
            athletes.append(dict(athlete=dict(id=f"{team}{index}", displayName=f"Player {team}{index}"),
                                 didNotPlay=False,
                                 stats=["48:00", str(10 + extra), "5-10", "0-2",
                                        "1-1" if extra else "0-0", "3", "2", "1", "0", "0"]))
        groups.append(dict(team=dict(id=team), statistics=[dict(
            labels=["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK"], athletes=athletes)]))
    return dict(header=dict(id="001", season=dict(year=2026, type=2), competitions=[dict(
        date="2025-10-22T02:00Z", neutralSite=False, status=dict(type=dict(completed=True)),
        competitors=[dict(homeAway=side, team=dict(id=team, displayName=side), score=score)
                     for side, team, score in (("home", "1", "51"), ("away", "2", "50"))])]),
        boxscore=dict(players=groups))


class PredictionHistoryTests(unittest.TestCase):
    def test_transport_rejects_http_errors_and_timeouts(self):
        for response in (CompletedProcess([], 0, b'{}\n429', b''),
                         CompletedProcess([], 28, b'\n000', b'')):
            with patch('courtside.predictions.history.subprocess.run', return_value=response):
                with self.assertRaises(ValueError):
                    fetch_bytes('https://example.com')
        with patch('courtside.predictions.history.subprocess.run', side_effect=TimeoutExpired('curl', 23)):
            with self.assertRaises(ValueError):
                fetch_bytes('https://example.com')

    def test_scores_ids_shooting_and_minutes(self):
        game, players = parse_game(summary(), "001")
        self.assertEqual(game["game_id"], "espn:001")
        self.assertEqual(game["season"], "2025-26")
        self.assertTrue(game["home_win"])
        self.assertEqual(len(players), 10)
        self.assertEqual(players[0]["minutes"], 48)
        self.assertEqual(players[0]["free_throws_made"], 1)

    def test_dnp_is_not_inferred_as_injury_or_player_log(self):
        payload = summary()
        payload["boxscore"]["players"][0]["statistics"][0]["athletes"].append(
            dict(athlete=dict(id="999"), didNotPlay=True, stats=[]))
        self.assertEqual(len(parse_game(payload, "001")[1]), 10)

    def test_incomplete_corrupt_and_nonfinal_games_are_rejected(self):
        mutations = [
            lambda p: p["header"].update(id="002"),
            lambda p: p["header"]["season"].update(type=3),
            lambda p: p["header"]["competitions"][0]["status"]["type"].update(completed=False),
            lambda p: p["header"]["competitions"][0].update(date="2025-10-21T20:00"),
            lambda p: p["header"]["competitions"][0]["competitors"][0].update(score="52"),
            lambda p: p["boxscore"]["players"].pop(),
            lambda p: p["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"].__setitem__(2, "11-10"),
            lambda p: p["boxscore"]["players"][0]["statistics"][0]["athletes"].append(
                deepcopy(p["boxscore"]["players"][0]["statistics"][0]["athletes"][0])),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                payload = summary()
                mutate(payload)
                with self.assertRaises(ValueError):
                    parse_game(payload, "001")

    def test_scoreboard_uses_eastern_date_and_rejects_duplicates(self):
        event = dict(id="001", date="2025-10-22T02:00Z")
        self.assertEqual(game_ids(dict(events=[event]), "2025-10-21"), ["001"])
        with self.assertRaises(ValueError):
            game_ids(dict(events=[event]), "2025-10-22")
        with self.assertRaises(ValueError):
            game_ids(dict(events=[event, event]), "2025-10-21")

    def test_cache_provenance_reuse_and_tampering(self):
        calls = []
        def transport(url):
            calls.append(url)
            return b'{"events": []}'
        with tempfile.TemporaryDirectory() as folder:
            first, metadata = read_source("scoreboard", "20251021", folder, transport)
            self.assertEqual(first, {"events": []})
            self.assertIn("retrieved_at", metadata)
            read_source("scoreboard", "20251021", folder, transport)
            self.assertEqual(len(calls), 1)
            (Path(folder) / "raw/scoreboard-20251021.json").write_text('{}')
            with self.assertRaisesRegex(ValueError, "checksum"):
                read_source("scoreboard", "20251021", folder, transport)

    def test_collection_reports_scope_and_retains_valid_games(self):
        events = [dict(id=key, date="2025-10-22T02:00Z") for key in ("001", "002")]
        def transport(url):
            if "scoreboard?" in url:
                return json.dumps(dict(events=events)).encode()
            return json.dumps(summary()).encode()
        with tempfile.TemporaryDirectory() as folder:
            report = collect("2025-10-21", folder, 1, transport, sleep=lambda _: None)
            self.assertEqual(report["validated_games"], 1)
            self.assertFalse(report["all_listed_games_validated"])
            report = collect("2025-10-21", folder, 2, transport, sleep=lambda _: None)
            self.assertEqual(report["validated_games"], 1)
            self.assertEqual(len(report["rejected"]), 1)
            self.assertTrue((Path(folder) / "sample-2025-10-21.json").exists())


if __name__ == "__main__":
    unittest.main()
