from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from .live_elo import collect_current_results, forecast_games
from .daily import build_daily

NOW = datetime(2026, 11, 15, 20, tzinfo=timezone.utc)
PARAMETERS = dict(initial_rating=1500, rating_scale=400, season_retention=0.75, k=20, home_advantage=50)


def fixtures():
    games = [dict(game_id=f'espn:{index}', season='2026-27',
        scheduled_start=(NOW - timedelta(days=7-index)).isoformat(),
        home=dict(team_id='espn:2', points=110), away=dict(team_id='espn:13', points=100),
        home_win=True, neutral_site=False) for index in range(6)]
    history = dict(games=games, season='2026-27', collected_at=NOW.isoformat())
    upcoming = [dict(game_id='espn:999', scheduled_start=(NOW + timedelta(hours=2)).isoformat(),
                     home_team_id='espn:2', away_team_id='espn:13', neutral_site=False)]
    return history, upcoming


class LiveEloTests(unittest.TestCase):
    def test_real_forecasts_use_available_history_and_preserve_inputs(self):
        history, upcoming = fixtures()
        before = deepcopy(history)
        row = forecast_games(history, upcoming, PARAMETERS, NOW)['predictions'][0]
        self.assertGreater(row['home_probability'], 0.5)
        self.assertAlmostEqual(row['home_rating'] + row['away_rating'], 3000)
        self.assertEqual(row['home_history_games'], 6)
        self.assertEqual(history, before)

    def test_delayed_and_future_results_do_not_change_forecast(self):
        history, upcoming = fixtures()
        first = forecast_games(history, upcoming, PARAMETERS, NOW)['predictions']
        for index, hours in enumerate((1, -1)):
            extra = deepcopy(history['games'][0])
            extra.update(game_id=f'espn:88{index}', scheduled_start=(NOW - timedelta(hours=hours)).isoformat())
            history['games'].append(extra)
        self.assertEqual(forecast_games(history, upcoming, PARAMETERS, NOW)['predictions'], first)

    def test_order_invariance(self):
        history, upcoming = fixtures()
        first = forecast_games(history, upcoming, PARAMETERS, NOW)['predictions'][0]
        history['games'].reverse()
        second = forecast_games(history, upcoming, PARAMETERS, NOW)['predictions'][0]
        self.assertAlmostEqual(first['home_probability'], second['home_probability'])

    def test_no_current_history_no_fake_forecast(self):
        history, upcoming = fixtures()
        history['games'] = history['games'][:4]
        result = forecast_games(history, upcoming, PARAMETERS, NOW)
        self.assertFalse(result['predictions'])
        self.assertIn('Fewer than five', result['excluded'][0]['reason'])

    def test_late_old_and_overlapping_games_rejected(self):
        history, upcoming = fixtures()
        upcoming[0]['scheduled_start'] = (NOW + timedelta(minutes=30)).isoformat()
        self.assertFalse(forecast_games(history, upcoming, PARAMETERS, NOW)['predictions'])
        history, upcoming = fixtures()
        upcoming[0]['game_id'] = history['games'][0]['game_id']
        with self.assertRaises(ValueError): forecast_games(history, upcoming, PARAMETERS, NOW)
        history, upcoming = fixtures()
        history['collected_at'] = (NOW - timedelta(hours=7)).isoformat()
        with self.assertRaises(ValueError): forecast_games(history, upcoming, PARAMETERS, NOW)

    def test_neutral_venue_has_no_home_bonus(self):
        history, upcoming = fixtures()
        home = forecast_games(history, upcoming, PARAMETERS, NOW)['predictions'][0]['home_probability']
        upcoming[0]['neutral_site'] = True
        neutral = forecast_games(history, upcoming, PARAMETERS, NOW)['predictions'][0]['home_probability']
        self.assertGreater(home, neutral)

    def test_collection_fails_on_missing_day_instead_of_partial_forecast(self):
        def transport(url):
            if '20261003' in url:
                raise ValueError('Source failed')
            return b'{"events": []}'
        with self.assertRaises(ValueError): collect_current_results(NOW, transport)

    def test_collection_only_requests_scoreboards_not_odds(self):
        urls = []
        def transport(url):
            urls.append(url)
            return json.dumps(dict(events=[])).encode()
        history = collect_current_results(NOW, transport)
        self.assertEqual(len(urls), 45)
        self.assertTrue(all('espn.com/' in url and 'scoreboard?dates=' in url for url in urls))
        self.assertEqual(history['coverage_end'], '2026-11-14')

    def test_collection_parses_final_scoreboard_contract(self):
        history, _ = fixtures()
        game = history['games'][0]
        event = dict(id='0', date=game['scheduled_start'], season=dict(year=2027, type=2),
            competitions=[dict(date=game['scheduled_start'], neutralSite=False,
                status=dict(type=dict(state='post', name='STATUS_FINAL', completed=True)),
                competitors=[dict(homeAway=side, team=dict(id=game[side]['team_id'][5:],
                    displayName=side), score=str(game[side]['points'])) for side in ('home', 'away')])])
        def transport(url):
            return json.dumps(dict(events=[event] if '20261108' in url else [])).encode()
        collected = collect_current_results(NOW, transport)
        self.assertEqual(len(collected['games']), 1)
        self.assertTrue(collected['games'][0]['home_win'])

    def test_live_forecast_consumed_by_daily_snapshot(self):
        history, upcoming = fixtures()
        forecasts = forecast_games(history, upcoming, PARAMETERS, NOW)
        event = dict(id='999', competitions=[dict(date=upcoming[0]['scheduled_start'],
            neutralSite=False, status=dict(type=dict(state='pre', name='STATUS_SCHEDULED')),
            competitors=[dict(homeAway=side, team=dict(id=team))
                         for side, team in (('home', '2'), ('away', '13'))])])
        matches = dict(generated_at=NOW.isoformat(), matched=[], schedule_sources=[
            dict(retrieved_at=NOW.isoformat(), raw=dict(events=[event]))])
        daily = build_daily(matches, '2026-11-15', NOW, forecasts)
        row = daily['games'][0]
        self.assertEqual(row['model_status'], 'available')
        self.assertEqual(row['home_probability'], forecasts['predictions'][0]['home_probability'])
        self.assertEqual(row['market_status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
