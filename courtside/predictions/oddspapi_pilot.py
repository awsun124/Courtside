"""Bounded retrospective FanDuel coverage pilot, not a full-season holdout."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode

from .baseline import scores
from .elo import predict_elo
from .history import timestamp
from .live_elo import frozen_parameters
from .odds import PROJECT_ROOT, decimal_odds
from .odds_matching import TEAM_IDS


class HistoricalRateLimit(ValueError):
    def __init__(self, seconds=6):
        super().__init__('OddsPapi historical endpoint cooldown')
        self.seconds = seconds


def load_key():
    key = os.environ.get('ODDSPAPI_API_KEY', '').strip()
    if not key:
        for line in (PROJECT_ROOT / '.env').read_text().splitlines():
            name, sep, value = line.partition('=')
            if sep and name.strip() == 'ODDSPAPI_API_KEY':
                key = value.strip().strip('\"\'')
                break
    if not key or any(character.isspace() for character in key):
        raise ValueError('Set ODDSPAPI_API_KEY in the server environment or .env')
    return key


def fetch_history(key, fixture_id, outcome_id, bookmaker):
    params = dict(apiKey=key, fixtureId=fixture_id, bookmakers=bookmaker)
    if outcome_id is None:
        params['playerId'] = 0
    else:
        params['outcomeId'] = outcome_id
    url = 'https://api.oddspapi.io/v4/historical-odds?' + urlencode(params)
    try:
        response = subprocess.run(['curl', '--disable', '--silent', '--compressed', '--proto', '=https',
            '--connect-timeout', '8', '--max-time', '45', '--max-filesize', '50000000', '--config', '-', '--write-out', '\n%{http_code}'],
            input='url = ' + json.dumps(url) + '\n', capture_output=True, text=True, timeout=48)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('OddsPapi connection failed; no retry was made') from None
    body, _, status = response.stdout.rpartition('\n')
    if response.returncode:
        raise ValueError('OddsPapi connection failed; no retry was made')
    if status == '404':
        return None
    if status == '429':
        try:
            error = json.loads(body) if key not in body else {}
        except ValueError:
            error = {}
        if error.get('code') == 'REQUEST_LIMIT_EXCEEDED':
            raise ValueError('OddsPapi monthly request allowance exhausted')
        delay = error.get('retryMs', error.get('error', {}).get('retryMs', 6000))
        seconds = max(6, min(60, delay / 1000)) if type(delay) in (int,float) else 6
        raise HistoricalRateLimit(seconds)
    if status != '200':
        raise ValueError('OddsPapi returned HTTP ' + (status if status.isdigit() else 'error'))
    if key in body:
        raise ValueError('Credential in provider response; response discarded')
    try:
        data = json.loads(body)
    except ValueError:
        raise ValueError('OddsPapi returned invalid JSON') from None
    if data.get('fixtureId') != fixture_id:
        raise ValueError('Historical response fixture differs from request')
    data['_response_sha256'] = hashlib.sha256(body.encode()).hexdigest()
    return data


def cutoff_price(payload, bookmaker, outcome_id, cutoff):
    if payload is None:
        return None
    rows = payload.get('bookmakers', {}).get(bookmaker, {}).get('markets', {}).get('111', {}).get(
        'outcomes', {}).get(str(outcome_id), {}).get('players', {}).get('0', [])
    prior = [row for row in rows if timestamp(row['createdAt']) <= cutoff]
    if not prior:
        return None
    latest = max(prior, key=lambda row: timestamp(row['createdAt']))
    # Do not resurrect an earlier active price after a suspension.
    if latest.get('active') is not True or cutoff - timestamp(latest['createdAt']) > timedelta(hours=6):
        return None
    return dict(price=decimal_odds(latest['price']), quoted_at=latest['createdAt'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures-file', type=Path, required=True)
    parser.add_argument('--history', type=Path, required=True)
    parser.add_argument('--bookmaker', default='fanduel')
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/oddspapi-pilot')
    args = parser.parse_args()
    try:
        now = datetime.now(timezone.utc)
        fixture_raw, history_raw = args.fixtures_file.read_bytes(), args.history.read_bytes()
        fixtures, history = json.loads(fixture_raw), json.loads(history_raw)
        if not 1 <= len(fixtures) <= 10 or any(row['tournamentId'] != 132 or row['sportId'] != 11 for row in fixtures):
            raise ValueError('Pilot requires one to ten NBA fixtures')
        if any(timestamp(row['startTime']) >= now or row.get('statusName') != 'Finished' for row in fixtures):
            raise ValueError('Pilot requires finished historical fixtures')
        if len({row['fixtureId'] for row in fixtures}) != len(fixtures):
            raise ValueError('Duplicate pilot fixture')
        if any(not re.fullmatch(r'id[0-9]+', row['fixtureId']) for row in fixtures):
            raise ValueError('Invalid provider fixture ID')
        games = history['games']
        if any(game['season'] != history['season'] for game in games):
            raise ValueError('Pilot history must contain one reset season')
        parameters, parameter_hash = frozen_parameters()
        predictions = predict_elo(games, parameters)
        key = load_key()
        run = args.output / now.strftime('%Y%m%dT%H%M%S%fZ')
        run.mkdir(parents=True, exist_ok=False)
        (run / 'fixtures.json').write_bytes(fixture_raw)
        (run / 'history.json').write_bytes(history_raw)
        matched, excluded, sources, previous_call = [], [], [], None
        for fixture in fixtures:
            home = 'espn:' + TEAM_IDS.get(fixture['participant1Name'], '')
            away = 'espn:' + TEAM_IDS.get(fixture['participant2Name'], '')
            candidates = [game for game in games if game['home']['team_id'] == home and game['away']['team_id'] == away
                          and abs(timestamp(game['scheduled_start']) - timestamp(fixture['startTime'])) <= timedelta(minutes=15)]
            if len(candidates) != 1:
                excluded.append(dict(fixture_id=fixture['fixtureId'], reason='No unique ESPN matchup'))
                continue
            game = candidates[0]
            cutoff = timestamp(predictions[game['game_id']]['cutoff_at'])
            counts = {side: sum(timestamp(prior['scheduled_start']) + timedelta(hours=24) <= cutoff
                               and game[side]['team_id'] in (prior['home']['team_id'], prior['away']['team_id'])
                               for prior in games) for side in ('home', 'away')}
            if min(counts.values()) < 5:
                excluded.append(dict(fixture_id=fixture['fixtureId'], reason='Insufficient pre-cutoff team history'))
                continue
            if any(not any(cutoff - timedelta(days=10) <= timestamp(prior['scheduled_start']) + timedelta(hours=24) <= cutoff
                           and game[side]['team_id'] in (prior['home']['team_id'], prior['away']['team_id'])
                           for prior in games) for side in ('home', 'away')):
                excluded.append(dict(fixture_id=fixture['fixtureId'], reason='Team history too old at cutoff'))
                continue
            prices = []
            for outcome_id in (111, 112):
                if previous_call is not None:
                    time.sleep(max(0, 5.1 - (time.monotonic() - previous_call)))
                previous_call = time.monotonic()
                data = fetch_history(key, fixture['fixtureId'], outcome_id, args.bookmaker)
                filename = fixture['fixtureId'] + '-' + str(outcome_id) + '.json'
                raw = json.dumps(data, indent=2) + '\n'
                (run / filename).write_text(raw)
                sources.append(dict(fixture_id=fixture['fixtureId'], outcome_id=outcome_id, file=filename,
                    retrieved_at=datetime.now(timezone.utc).isoformat(), sha256=hashlib.sha256(raw.encode()).hexdigest()))
                prices.append(cutoff_price(data, args.bookmaker, outcome_id, cutoff))
            if any(price is None for price in prices):
                excluded.append(dict(fixture_id=fixture['fixtureId'], reason='No eligible two-sided pre-cutoff moneyline'))
                print('Excluded', game['game_id'], flush=True)
                continue
            home_raw, away_raw = (1 / price['price'] for price in prices)
            market = home_raw / (home_raw + away_raw)
            probability = predictions[game['game_id']]['home_probability']
            value_side = 'home' if probability > market else 'away' if probability < market else None
            matched.append(dict(game_id=game['game_id'], fixture_id=fixture['fixtureId'],
                home_team=fixture['participant1Name'], away_team=fixture['participant2Name'],
                cutoff_at=cutoff.isoformat(), target=int(game['home_win']), model_probability=probability,
                market_probability=market, home_quote=prices[0], away_quote=prices[1],
                undervalued_side=value_side, undervalued_pick_won=(game['home_win'] == (value_side == 'home')) if value_side else None))
            print('Scored', game['game_id'], flush=True)
        labels = [row['target'] for row in matched]
        picks = [row for row in matched if row['undervalued_side']]
        report = dict(bookmaker=args.bookmaker, evaluated_at=now.isoformat(), model='elo-current-season-reset-v1',
            requested_games=len(fixtures), matched_games=len(matched), excluded=excluded, predictions=matched,
            parameters=parameters, frozen_elo_report_sha256=parameter_hash, sources=sources,
            history_sha256=hashlib.sha256(history_raw).hexdigest(), fixtures_sha256=hashlib.sha256(fixture_raw).hexdigest(),
            model_scores=scores(labels, [row['model_probability'] for row in matched]) if matched else None,
            market_scores=scores(labels, [row['market_probability'] for row in matched]) if matched else None,
            undervalued_picks=dict(games=len(picks), wins=sum(row['undervalued_pick_won'] for row in picks),
                win_rate=sum(row['undervalued_pick_won'] for row in picks) / len(picks) if picks else None),
            limitations=['Small recent-game coverage pilot, not a full-season or untouched holdout claim.',
                         'This evaluates the current-season reset Elo baseline, not player/synergy models.',
                         'Both bookmaker sides use the latest active state available at the ESPN one-hour cutoff.',
                         'Quotes older than six hours at cutoff are excluded; actual quote timestamps are retained.',
                         'Historical Elo uses a 24-hour assumed result delay; historical publication times are unverified.',
                         'Undervalued-pick win rate is not evidence of profitability.'])
        (run / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        print(json.dumps({k: report[k] for k in ('matched_games', 'model_scores', 'market_scores', 'undervalued_picks')}, indent=2))
        print('Report:', run / 'report.json')
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as error:
        parser.exit(2, f'Historical pilot failed: {error}\n')


if __name__ == '__main__':
    main()
