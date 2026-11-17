from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .production import update_results,restore_odds
from .repository import migrate,load_outcomes,put_state

NOW=datetime(2026,11,16,23,tzinfo=timezone.utc)


def event(key,day,completed=True):
    start=day+'T19:00:00Z'
    return dict(id=key,date=start,season=dict(type=2,year=2027),competitions=[dict(date=start,
        status=dict(type=dict(completed=completed,name='STATUS_FINAL' if completed else 'STATUS_SCHEDULED')),
        competitors=[dict(homeAway='home',score='110',team=dict(id='2',displayName='Boston Celtics')),
                     dict(homeAway='away',score='100',team=dict(id='13',displayName='Los Angeles Lakers'))])])


class ProductionResultTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.db=Path(self.folder.name)/'results.db';migrate(self.db)
        put_state(self.db,'prediction-result-coverage',dict(through='2026-11-15'),NOW)

    def scoreboard(self,day):
        events=[event('100',day.isoformat()),event('101',day.isoformat(),False)] if day.isoformat()=='2026-11-16' else []
        return dict(events=events),dict(url='https://example.org/scoreboard',retrieved_at=NOW.isoformat(),sha256='source',raw={})

    def test_final_games_today_settle_and_upcoming_games_stay_pending(self):
        with patch('courtside.predictions.production.get_scoreboard',side_effect=self.scoreboard):
            results=update_results(self.db,NOW)
        self.assertEqual([r['game_id'] for r in results],['espn:100'])
        self.assertTrue(results[0]['home_win'])

    def test_odds_cache_restores_on_a_fresh_worker(self):
        import json
        put_state(self.db,'prediction-odds-cache',{'snapshots/example.json':{'games':[]}},NOW)
        folder=Path(self.folder.name)/'new-worker'/'odds'
        restore_odds(self.db,folder)
        self.assertEqual(json.loads((folder/'snapshots/example.json').read_text()),{'games':[]})

    def test_incomplete_older_result_stops_new_forecasts(self):
        def invalid(day):
            return dict(events=[event('102',day.isoformat(),False)]),{}
        with patch('courtside.predictions.production.get_scoreboard',side_effect=invalid):
            with self.assertRaises(ValueError):update_results(self.db,NOW)
        self.assertEqual(load_outcomes(self.db),[])
