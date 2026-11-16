"""Offline contract tests for conservative provider matching."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from .odds_matching import TEAM_IDS, match_odds, parse_schedule

NOW = datetime(2026, 11, 15, 20, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=2)).isoformat()


def fixtures():
    snapshot = dict(retrieved_at=NOW.isoformat(), games=[dict(
        game_id='odds:abc', commence_time=START, home_team='Boston Celtics',
        away_team='Los Angeles Lakers', bookmakers=[dict(bookmaker='example',
            available_at=NOW.isoformat(), home_decimal_odds=1.8, away_decimal_odds=2.2)])])
    schedule = [dict(game_id='espn:123', scheduled_start=START,
                     home_team_id='espn:2', away_team_id='espn:13')]
    return snapshot, schedule


class OddsMatchingTests(unittest.TestCase):
    def test_mapping_covers_all_thirty_teams(self):
        self.assertEqual(len(set(TEAM_IDS.values())), 30)

    def test_unique_match_preserves_prices_and_does_not_mutate_snapshot(self):
        snapshot, schedule = fixtures()
        original = deepcopy(snapshot)
        report = match_odds(snapshot, schedule, NOW)
        self.assertEqual(report['matched'][0]['game_id'], 'espn:123')
        self.assertEqual(report['matched'][0]['bookmakers'][0]['home_decimal_odds'], 1.8)
        self.assertTrue(report['matched'][0]['bookmakers'][0]['benchmark_eligible'])
        self.assertEqual(snapshot, original)

    def test_unknown_names_reversed_sides_and_time_mismatch_do_not_match(self):
        for change in ('name', 'sides', 'time'):
            snapshot, schedule = fixtures()
            if change == 'name':
                snapshot['games'][0]['home_team'] = 'Boston'
            elif change == 'sides':
                schedule[0].update(home_team_id='espn:13', away_team_id='espn:2')
            else:
                schedule[0]['scheduled_start'] = (NOW + timedelta(hours=3)).isoformat()
            report = match_odds(snapshot, schedule, NOW)
            self.assertFalse(report['matched'])
            self.assertEqual(len(report['unmatched']), 1)

    def test_tolerance_boundary(self):
        snapshot, schedule = fixtures()
        for seconds, expected in ((900, 1), (901, 0)):
            schedule[0]['scheduled_start'] = (NOW + timedelta(hours=2, seconds=seconds)).isoformat()
            self.assertEqual(len(match_odds(snapshot, schedule, NOW)['matched']), expected)

    def test_duplicate_and_ambiguous_matches_are_rejected(self):
        for duplicate in ('schedule', 'odds', 'ambiguous', 'many_to_one'):
            snapshot, schedule = fixtures()
            if duplicate in ('schedule', 'ambiguous'):
                schedule.append(dict(schedule[0]))
                if duplicate == 'ambiguous':
                    schedule[1]['game_id'] = 'espn:456'
            else:
                snapshot['games'].append(deepcopy(snapshot['games'][0]))
                if duplicate == 'many_to_one':
                    snapshot['games'][1]['game_id'] = 'odds:def'
            self.assertFalse(match_odds(snapshot, schedule, NOW)['matched'])

    def test_stale_and_future_quotes_are_ineligible(self):
        for age in (timedelta(hours=7), timedelta(seconds=-1)):
            snapshot, schedule = fixtures()
            snapshot['games'][0]['bookmakers'][0]['available_at'] = (NOW - age).isoformat()
            book = match_odds(snapshot, schedule, NOW)['matched'][0]['bookmakers'][0]
            self.assertFalse(book['benchmark_eligible'])

    def test_started_games_and_future_snapshots(self):
        snapshot, schedule = fixtures()
        self.assertFalse(match_odds(snapshot, schedule, NOW + timedelta(hours=2))['matched'])
        with self.assertRaises(ValueError):
            match_odds(snapshot, schedule, NOW - timedelta(seconds=1))

    def test_schedule_parser_skips_postponed_and_invalid_games(self):
        event = dict(id='123', competitions=[dict(date=START,
            status=dict(type=dict(state='pre', name='STATUS_SCHEDULED')),
            competitors=[dict(homeAway='home', team=dict(id='2')),
                         dict(homeAway='away', team=dict(id='13'))])])
        postponed = deepcopy(event)
        postponed['competitions'][0]['status']['type']['name'] = 'STATUS_POSTPONED'
        games, rejected = parse_schedule(dict(events=[event, postponed, dict(id='bad')]))
        self.assertEqual(len(games), 1)
        self.assertEqual(len(rejected), 1)


if __name__ == '__main__':
    unittest.main()
