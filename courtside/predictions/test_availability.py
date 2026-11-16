from copy import deepcopy
from datetime import datetime,timedelta,timezone
import unittest
from unittest.mock import patch,MagicMock

from .availability import adjust_forecasts,name_key,parse_report

NOW=datetime(2026,11,16,16,tzinfo=timezone.utc)


def fixture(star_absent=False,side='home',status='Out'):
    games=[];logs=[]
    for i in range(10):
        game=dict(game_id=f'espn:{i}',season='2026-27',scheduled_start=(NOW-timedelta(days=12-i)).isoformat(),
            home=dict(team_id='espn:2',points=120),away=dict(team_id='espn:13',points=110),home_win=True)
        games.append(game)
        for team in ('espn:2','espn:13'):
            for j in range(8):
                if j==0 and star_absent and i>=5:continue
                star=j==0
                logs.append(dict(game_id=game['game_id'],team_id=team,player_id=team+str(j),name='Stephen Curry' if star else f'Bench {j}',
                    minutes=36 if star else 20,points=32 if star else 6,rebounds=5,assists=7 if star else 1,
                    turnovers=2,steals=1,blocks=0,field_goals_made=11 if star else 2,field_goals_attempted=20 if star else 6,
                    free_throws_made=5,free_throws_attempted=6))
    target='espn:2' if side=='home' else 'espn:13'
    report=dict(published_at=(NOW-timedelta(minutes=15)).isoformat(),source=dict(url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_test.pdf'),
        teams=[dict(date='2026-11-16',matchup='LAL@BOS',team_id=t,status='submitted') for t in ('espn:2','espn:13')],
        entries=[dict(date='2026-11-16',matchup='LAL@BOS',team_id=target,name='Curry, Stephen',name_key=name_key('Curry, Stephen'),status=status)])
    row=dict(game_id='espn:999',scheduled_start=(NOW+timedelta(hours=6)).isoformat(),cutoff_at=(NOW+timedelta(hours=5)).isoformat(),
        home_team_id='espn:2',away_team_id='espn:13',home_probability=.65)
    forecasts=dict(model_version='margin-strength-v1',predictions=[row])
    return forecasts,report,dict(games=deepcopy(games),players=logs),games


class AvailabilityTests(unittest.TestCase):
    def test_pdf_continuations_keep_team_and_date_and_reject_wrong_publication(self):
        def word(text,x,y):return dict(text=text,x0=x,top=y)
        first=[word('Team',264,108),word('Matchup',200,108),word('Reason',666,108),
               word('11/16/2026',24,129),word('LAL@BOS',201,129),word('Boston Celtics',265,129),
               word('Curry, Stephen',426,129),word('Out',587,129)]
        second=[word('Bench, One',426,84),word('Questionable',587,84),
                word('Los Angeles Lakers',265,110),word('NOT YET SUBMITTED',426,110)]
        pages=[]
        for i,words in enumerate((first,second)):
            p=MagicMock(page_number=i+1,width=842,height=595)
            p.extract_words.return_value=words;p.extract_text.return_value='Injury Report: 11/16/26 10:45 AM'
            pages.append(p)
        context=MagicMock();context.__enter__.return_value.pages=pages
        with patch('pdfplumber.open',return_value=context):
            report=parse_report(b'pdf','2026-11-16T10:45:00-05:00')
            self.assertEqual(len(report['entries']),2)
            self.assertEqual(report['entries'][1]['team_id'],'espn:2')
            self.assertEqual(report['teams'][1]['status'],'not_submitted')
            with self.assertRaises(ValueError):parse_report(b'pdf','2026-11-16T11:00:00-05:00')

    def test_star_out_lowers_own_probability_and_preserves_base(self):
        args=fixture();before=deepcopy(args[0]);result=adjust_forecasts(*args,NOW)
        row=result['predictions'][0]
        self.assertLess(row['home_probability'],.65)
        self.assertGreater(row['availability']['players'][0]['estimated_margin_impact'],0)
        self.assertEqual(row['availability']['base_home_probability'],.65)
        self.assertEqual(args[0],before)
        self.assertEqual(result['model_version'],'margin-strength-availability-v1')

    def test_opponent_star_out_raises_home_probability(self):
        row=adjust_forecasts(*fixture(side='away'),NOW)['predictions'][0]
        self.assertGreater(row['home_probability'],.65)

    def test_long_absence_is_not_penalized_again(self):
        row=adjust_forecasts(*fixture(star_absent=True),NOW)['predictions'][0]
        self.assertEqual(row['home_probability'],.65)
        self.assertEqual(row['availability']['players'][0]['recent_minutes'],0)

    def test_questionable_does_not_become_confirmed_out(self):
        row=adjust_forecasts(*fixture(status='Questionable'),NOW)['predictions'][0]
        self.assertEqual(row['home_probability'],.65)
        self.assertEqual(row['availability']['players'][0]['status'],'Questionable')

    def test_future_or_stale_report_never_changes_prediction(self):
        for delta in (timedelta(seconds=1),timedelta(hours=-7)):
            args=list(fixture());args[1]['published_at']=(NOW+delta).isoformat()
            row=adjust_forecasts(*args,NOW)['predictions'][0]
            self.assertEqual(row['home_probability'],.65)
            self.assertEqual(row['availability']['status'],'unavailable')

    def test_unsubmitted_and_missing_boxscore_not_inferred_healthy(self):
        args=list(fixture());args[1]['teams'][0]['status']='not_submitted'
        row=adjust_forecasts(*args,NOW)['predictions'][0]
        self.assertEqual(row['home_probability'],.65)
        self.assertEqual(row['availability']['coverage'],'partial')
        args=list(fixture());args[2]['games'].pop()
        row=adjust_forecasts(*args,NOW)['predictions'][0]
        self.assertEqual(row['home_probability'],.65)
        self.assertEqual(row['availability']['coverage'],'partial')

    def test_unknown_and_ambiguous_names_do_not_fuzzy_match(self):
        args=list(fixture());args[1]['entries'][0]['name_key']=name_key('Curry, Seth')
        row=adjust_forecasts(*args,NOW)['predictions'][0]
        self.assertEqual(row['home_probability'],.65)
        self.assertEqual(len(row['availability']['unmatched_players']),1)

    def test_future_player_games_never_enter_adjustment(self):
        args=list(fixture());before=adjust_forecasts(*args,NOW)['predictions'][0]
        future=deepcopy(args[3][-1]);future.update(game_id='espn:888',scheduled_start=NOW.isoformat())
        args[3].append(future);args[2]['games'].append(future)
        for p in args[2]['players'][:8]:
            args[2]['players'].append(dict(p,game_id='espn:888',points=100))
        after=adjust_forecasts(*args,NOW)['predictions'][0]
        self.assertEqual(before,after)

    def test_name_suffixes_and_accents(self):
        self.assertEqual(name_key('Butler III, Jimmy'),name_key('Jimmy Butler III'))
        self.assertEqual(name_key('Dončić, Luka'),name_key('Luka Doncic'))


if __name__=='__main__':unittest.main()
