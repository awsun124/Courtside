"""Public, read-only projection of saved daily prediction snapshots."""

from datetime import date, timedelta
import json
from pathlib import Path
import re

from .daily import probability
from .history import EASTERN, timestamp
from .odds import MIN_INTERVAL, decimal_odds
from .odds_matching import TEAM_IDS

TEAM_NAMES = {}
for name, team_id in TEAM_IDS.items():
    TEAM_NAMES.setdefault('espn:' + team_id, name)


def public_game(game, saved_at, now):
    start = timestamp(game['scheduled_start'])
    cutoff = timestamp(game['cutoff_at'])
    if cutoff != start - timedelta(hours=1):
        raise ValueError('Invalid cutoff')
    available = game['model_status'] == 'available'
    if game['model_status'] not in {'available', 'unavailable'}:
        raise ValueError('Unknown model status')
    home = probability(game['home_probability']) if available else None
    if available and (timestamp(saved_at) > cutoff or
                      timestamp(game['inputs_as_of']) > timestamp(game['forecast_generated_at']) or
                      timestamp(game['forecast_generated_at']) > timestamp(saved_at)):
        raise ValueError('Invalid saved forecast timing')
    row = {key: game[key] for key in ('game_id', 'scheduled_start', 'cutoff_at', 'model_status')}
    row.update(home_team=TEAM_NAMES[game['home_team_id']], away_team=TEAM_NAMES[game['away_team_id']],
               home_probability=home, away_probability=1 - home if home is not None else None,
               model_version=game.get('model_version') if available else None,
               model_reason=game.get('model_reason'), saved_at=saved_at,
               forecast_generated_at=game.get('forecast_generated_at') if available else None,
               inputs_as_of=game.get('inputs_as_of') if available else None,
               tipoff_passed=start <= now, bookmakers=[])
    if available and game.get('availability'):
        injury=game['availability']
        row['availability']={key:injury.get(key) for key in ('status','base_home_probability','home_probability_change',
            'experimental','coverage','reason','report_published_at')}
        # Public projection excludes source paths and only permits the official report host.
        url=injury.get('report_url','') or ''
        if url.startswith('https://ak-static.cms.nba.com/referee/injury/Injury-Report_'):
            row['availability']['report_url']=url
        row['availability']['players']=[{key:p.get(key) for key in ('name','status','team_id','recent_ppg','recent_minutes','estimated_margin_impact')}
            for p in injury.get('players',[])]
    for book in game['bookmakers']:
        available_at, retrieved = timestamp(book['available_at']), timestamp(book['retrieved_at'])
        eligible = book['benchmark_eligible'] is True
        if eligible and not available_at <= retrieved <= timestamp(saved_at) <= cutoff:
            raise ValueError('Invalid saved quote timing')
        stale = now - available_at > MIN_INTERVAL or now - retrieved > MIN_INTERVAL
        home_market = probability(book['home_market_probability'])
        away_market = probability(book['away_market_probability'])
        if abs(home_market + away_market - 1) > 1e-6:
            raise ValueError('Invalid market probability pair')
        row['bookmakers'].append(dict(bookmaker=book['bookmaker'],
            available_at=book['available_at'], retrieved_at=book['retrieved_at'],
            home_decimal_odds=decimal_odds(book['home_decimal_odds']), away_decimal_odds=decimal_odds(book['away_decimal_odds']),
            home_market_probability=home_market, away_market_probability=away_market,
            benchmark_eligible_at_save=eligible, stale=stale,
            current=eligible and not stale and start > now,
            home_difference_percentage_points=100 * (home - home_market) if home is not None and eligible else None))
    return row


def read_predictions(folder, day, now):
    """Keep the first available forecast per game across immutable daily runs.

    A later run may omit started games or lose inputs. Neither should erase an
    already recorded pregame forecast. Its paired market stays with that forecast.
    """
    day = date.fromisoformat(day).isoformat()
    result = dict(date=day, timezone='America/New_York', checked_at=now.isoformat(),
                  saved_at=None, status='no_snapshot', games=[])
    directory = Path(folder) / day
    if not directory.exists():
        return result
    records = []
    for path in sorted(directory.glob('*.json')):
        if not re.fullmatch(r'\d{8}T\d{12}Z\.json', path.name):
            continue
        if path.is_symlink() or path.stat().st_size > 5_000_000:
            raise ValueError('Invalid snapshot file')
        payload = json.loads(path.read_text())
        saved = timestamp(payload['saved_at'])
        if (payload['schema_version'] != 1 or payload['date'] != day or
                payload['timezone'] != 'America/New_York' or saved > now):
            raise ValueError('Invalid snapshot metadata')
        records.append((saved, payload))
    games = {}
    for _, payload in sorted(records, key=lambda item: item[0]):
        seen = set()
        for game in payload['games']:
            key = game['game_id']
            if key in seen or timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat() != day:
                raise ValueError('Invalid or duplicate snapshot game')
            seen.add(key)
            public = public_game(game, payload['saved_at'], now)
            if key not in games or games[key]['model_status'] != 'available':
                games[key] = public
        result.update(status='available', saved_at=payload['saved_at'])
    result['games'] = sorted(games.values(), key=lambda row: (timestamp(row['scheduled_start']), row['game_id']))
    return result
