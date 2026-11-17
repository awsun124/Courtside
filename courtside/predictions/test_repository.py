from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from .repository import (migrate,save_snapshot,read_day,performance,save_outcomes,claim_job,release_job)
from .daily import build_daily
from .test_daily import fixtures,NOW


class PredictionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'test.db';migrate(self.db);migrate(self.db)
        self.matches,self.forecasts=fixtures()

    def save(self,p=.6,minutes=0):
        self.forecasts['predictions'][0]['home_probability']=p
        report=build_daily(self.matches,'2026-11-15',NOW+timedelta(minutes=minutes),self.forecasts)
        save_snapshot(self.db,report,NOW+timedelta(minutes=minutes))
        return report

    def test_idempotent_snapshots_and_latest_on_time_forecast(self):
        report=self.save();save_snapshot(self.db,report,NOW)
        self.save(.7,30)
        result=read_day(self.db,'2026-11-15',NOW+timedelta(hours=3))
        self.assertEqual(len(result['games']),1);self.assertEqual(result['games'][0]['home_probability'],.7)

    def test_late_or_missing_forecast_never_replaces_valid_one(self):
        self.save(.7)
        late=build_daily(self.matches,'2026-11-15',NOW+timedelta(minutes=65),self.forecasts)
        save_snapshot(self.db,late,NOW+timedelta(minutes=65))
        self.assertEqual(read_day(self.db,'2026-11-15',NOW+timedelta(hours=3))['games'][0]['home_probability'],.7)

    def test_prospective_performance_and_result_use_same_frozen_snapshot(self):
        self.save(.4);self.save(.7,30)
        self.assertEqual(performance(self.db,NOW)['games'],0)
        game=dict(game_id='espn:123',season='2026-27',scheduled_start=self.forecasts['predictions'][0]['scheduled_start'],
            home=dict(team_id='espn:2',points=110),away=dict(team_id='espn:13',points=100),home_win=True)
        later=NOW+timedelta(hours=4);save_outcomes(self.db,[game],later)
        result=performance(self.db,later)
        self.assertEqual(result['games'],1);self.assertEqual(result['correct'],1)
        self.assertAlmostEqual(result['brier'],.09)
        self.assertTrue(read_day(self.db,'2026-11-15',later)['games'][0]['result']['correct'])
        # A second result import cannot double-count a game.
        save_outcomes(self.db,[game],later);self.assertEqual(performance(self.db,later)['games'],1)

    def test_future_observations_and_unavailable_games_are_not_scored(self):
        self.save(.7,30)
        self.assertEqual(read_day(self.db,'2026-11-15',NOW)['status'],'no_snapshot')
        self.assertEqual(performance(self.db,NOW)['pending_games'],0)

    def test_collection_lease_is_exclusive_and_expires(self):
        self.assertTrue(claim_job(self.db,NOW));self.assertFalse(claim_job(self.db,NOW))
        self.assertTrue(claim_job(self.db,NOW+timedelta(minutes=21)))
        release_job(self.db);self.assertTrue(claim_job(self.db,NOW+timedelta(minutes=22)))

    def test_mismatched_dates_and_duplicate_games_rollback(self):
        report=build_daily(self.matches,'2026-11-15',NOW,self.forecasts)
        report['games'].append(deepcopy(report['games'][0]))
        with self.assertRaises(ValueError):save_snapshot(self.db,report,NOW)
        self.assertEqual(read_day(self.db,'2026-11-15',NOW)['games'],[])


class PortableModelTests(unittest.TestCase):
    def test_bundled_model_reproduces_recorded_reference_forecasts(self):
        from .production_model import load_model
        from .strength import build_strength_rows
        from .improve import predict
        from threadpoolctl import threadpool_limits
        metadata,model,history=load_model()
        with threadpool_limits(limits=1):
            rows=build_strength_rows(history)
            values=predict(rows[-3:],metadata['selected']['spec'],model)
        self.assertEqual(len(history),metadata['history_games'])
        self.assertTrue(all(0<p<1 for p in values))
