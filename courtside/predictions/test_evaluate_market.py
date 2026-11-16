import unittest

from .daily import build_daily
from .evaluate_market import compare
from .service import public_game
from .test_daily import fixtures, NOW


class MarketEvaluationTests(unittest.TestCase):
    def fixture(self):
        matches, forecasts = fixtures()
        saved = build_daily(matches, '2026-11-15', NOW, forecasts)
        game = public_game(saved['games'][0], saved['saved_at'], NOW)
        game['bookmakers'][0]['bookmaker'] = 'fanduel'
        result = dict(home=dict(team_id='espn:2'), away=dict(team_id='espn:13'),
                      home_win=True, scheduled_start=game['scheduled_start'])
        return game, result

    def test_empty_and_unsettled_are_unavailable_not_zero_accuracy(self):
        game, _ = self.fixture()
        report = compare([game], {})
        self.assertIsNone(report['model'])
        self.assertIsNone(report['undervalued_picks']['win_rate'])
        self.assertEqual(report['pending_game_ids'], ['espn:123'])

    def test_scores_same_games_and_counts_undervalued_side(self):
        game, result = self.fixture()
        report = compare([game], {'espn:123': result})
        self.assertEqual(report['model']['accuracy'], 1)
        self.assertAlmostEqual(report['model']['brier'], 0.16)
        self.assertAlmostEqual(report['market']['brier'], 0.2025)
        self.assertEqual(report['undervalued_picks']['win_rate'], 1)
        game['home_probability'] = 0.5
        report = compare([game], {'espn:123': result})
        self.assertEqual(report['predictions'][0]['undervalued_side'], 'away')
        self.assertEqual(report['undervalued_picks']['win_rate'], 0)

    def test_missing_odds_excluded_and_ties_are_not_value_picks(self):
        game, result = self.fixture()
        game['home_probability'] = 0.55
        report = compare([game], {'espn:123': result})
        self.assertEqual(report['settled_games'], 1)
        self.assertEqual(report['undervalued_picks']['games'], 0)
        game['bookmakers'][0]['benchmark_eligible_at_save'] = False
        report = compare([game], {'espn:123': result})
        self.assertEqual(report['settled_games'], 0)
        self.assertEqual(len(report['excluded']), 1)

    def test_wrong_teams_and_duplicate_games_fail(self):
        game, result = self.fixture()
        with self.assertRaises(ValueError): compare([game, game], {})
        result['home']['team_id'] = 'espn:13'
        with self.assertRaises(ValueError): compare([game], {'espn:123': result})


if __name__ == '__main__':
    unittest.main()
