from copy import deepcopy
from datetime import timedelta
import unittest
from contextlib import redirect_stdout
import io
import json
import tempfile
import hashlib
from pathlib import Path
from unittest.mock import patch

from .market_backtest import evaluate, score, uncertainty
from .test_live_elo import fixtures, PARAMETERS, NOW


def inputs():
    history, upcoming = fixtures()
    game = deepcopy(history['games'][0])
    game.update(game_id='espn:999', scheduled_start=upcoming[0]['scheduled_start'])
    game['home']['name'], game['away']['name'] = 'Boston Celtics', 'Los Angeles Lakers'
    games = history['games'] + [game]
    fixture = dict(fixtureId='id123', participant1Name='Boston Celtics',
                   participant2Name='Los Angeles Lakers', startTime=game['scheduled_start'])
    quote_at = (NOW + timedelta(minutes=59)).isoformat()
    payload = dict(bookmakers={'fanduel': dict(markets={'111': dict(outcomes={
        str(outcome): dict(players={'0': [dict(createdAt=quote_at, price=price, active=True)]})
        for outcome, price in ((111, 1.8), (112, 2.2))})})})
    quotes = {'id123': dict(data=payload)}
    plan = dict(test_start='2026-11-15', test_end='2026-11-15', minimum_history=5,
                bookmaker='fanduel', bootstrap_seed=42, bootstrap_resamples=100)
    return games, [fixture], quotes, plan


class FullPeriodMarketTests(unittest.TestCase):
    def test_preseason_date_spill_does_not_invalidate_regular_season_inventory(self):
        from scripts.collect_prediction_results import collect_results
        event = dict(id='9', date='2025-10-03T09:30Z', season=dict(type=1))
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            result = collect_results('2025-10-02','2025-10-02',folder,
                transport=lambda _: json.dumps(dict(events=[event])).encode(), sleep=lambda _:None)
        self.assertTrue(result['complete'])
        self.assertEqual(result['validated_games'],0)
        event['season']['type'] = 2
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            result = collect_results('2025-10-02','2025-10-02',folder,
                transport=lambda _: json.dumps(dict(events=[event])).encode(), sleep=lambda _:None)
        self.assertFalse(result['complete'])

    def test_matching_coverage_and_probability_pair(self):
        games, fixtures, quotes, plan = inputs()
        result = evaluate(games, fixtures, quotes, plan, PARAMETERS)
        self.assertEqual(result['expected_espn_games'], 1)
        self.assertEqual(result['matched_games'], 1)
        self.assertAlmostEqual(result['predictions'][0]['market_probability'], 0.55)
        self.assertEqual(result['undervalued_picks']['games'], 1)

    def test_current_outcome_does_not_change_its_own_prediction(self):
        games, fixtures, quotes, plan = inputs()
        first = evaluate(games, fixtures, quotes, plan, PARAMETERS)
        games[-1]['home_win'] = False
        games[-1]['home']['points'], games[-1]['away']['points'] = 90, 100
        second = evaluate(games, fixtures, quotes, plan, PARAMETERS)
        self.assertEqual(first['predictions'][0]['model_probability'], second['predictions'][0]['model_probability'])

    def test_missing_and_duplicate_fixture_matches_are_excluded_explicitly(self):
        games, fixtures, quotes, plan = inputs()
        for inventory in ([], fixtures * 2):
            result = evaluate(games, inventory, quotes, plan, PARAMETERS)
            self.assertEqual(result['matched_games'], 0)
            self.assertEqual(len(result['excluded']), 1)
            self.assertIsNone(result['model_scores'])

    def test_after_cutoff_quotes_never_enter_score(self):
        games, fixtures, quotes, plan = inputs()
        row = quotes['id123']['data']['bookmakers']['fanduel']['markets']['111']['outcomes']['111']['players']['0'][0]
        row['createdAt'] = (NOW + timedelta(minutes=61)).isoformat()
        result = evaluate(games, fixtures, quotes, plan, PARAMETERS)
        self.assertEqual(result['matched_games'], 0)
        self.assertIn('cutoff odds', result['excluded'][0]['reason'])

    def test_paired_uncertainty_preserves_sign_and_is_deterministic(self):
        rows = [dict(date=f'2026-01-{i:02}', target=1, model_probability=0.8, market_probability=0.5)
                for i in range(1, 6)]
        first = uncertainty(rows, resamples=100)
        self.assertAlmostEqual(first['model_minus_market_brier_mean'], -0.21)
        self.assertLess(first['model_minus_market_brier_95_interval'][1], 0)
        self.assertEqual(first, uncertainty(rows, resamples=100))

    def test_complete_cached_run_exports_report_and_game_csv(self):
        games, fixtures, quotes, plan = inputs()
        plan.update(history_start='2026-11-01', result_delay_hours=24, cutoff_minutes=60,
                    maximum_quote_age_hours=6, model='elo-current-season-reset-v1')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'results').mkdir()
            (root/'quotes').mkdir()
            (root/'plan.json').write_text(json.dumps(plan))
            (root/'fixtures.json').write_text(json.dumps(fixtures))
            history=dict(coverage=dict(complete=True), games=games)
            (root/'results'/'results-2026-11-01-2026-11-15.json').write_text(json.dumps(history))
            quote=dict(quotes['id123'],fixture_id='id123',bookmaker='fanduel',status='received')
            (root/'quotes'/'id123.json').write_text(json.dumps(quote))
            with patch('courtside.predictions.market_backtest.frozen_parameters',return_value=(PARAMETERS,'test-hash')), redirect_stdout(io.StringIO()):
                report,path=score(root)
            self.assertEqual(report['matched_games'],1)
            self.assertEqual(report['coverage_by_date'][0]['matched'],1)
            self.assertTrue(path.with_suffix('.md').is_file())
            self.assertIn('model_probability',path.with_suffix('.csv').read_text())
            self.assertIn('history',report['source_hashes'])
            previous=deepcopy(games[0])
            previous.update(game_id='espn:11111',season='2025-26',scheduled_start='2026-04-01T20:00:00Z')
            raw=json.dumps(dict(coverage=dict(complete=True),games=[previous])).encode()
            (root/'carryover-history.json').write_bytes(raw)
            (root/'secondary-plan.json').write_text(json.dumps(dict(history_sha256=hashlib.sha256(raw).hexdigest())))
            with patch('courtside.predictions.market_backtest.frozen_parameters',return_value=(PARAMETERS,'test-hash')), redirect_stdout(io.StringIO()):
                report,_=score(root)
            self.assertEqual(report['secondary_carryover_elo']['matched_games'],report['matched_games'])


if __name__ == '__main__':
    unittest.main()
