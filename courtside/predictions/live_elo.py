"""Current-season Elo inference and immutable daily snapshots, without fitting."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .daily import build_daily, save_daily
from .elo import predict_elo
from .features import validate_games
from .history import BASE_URL, EASTERN, fetch_bytes, parse_result, timestamp
from .odds import PROJECT_ROOT
from .odds_matching import parse_schedule

MODEL_VERSION = 'elo-current-season-reset-v1'


def collect_current_results(now, transport=fetch_bytes):
    """Fetch one scoreboard per day; no odds requests or per-game requests."""
    today = now.astimezone(EASTERN).date()
    year = today.year if today.month >= 10 else today.year - 1
    first = today.replace(year=year, month=10, day=1)
    if (today - first).days > 250:
        raise ValueError('Outside the supported October-June regular-season collection window')
    games, sources, excluded, pending, seen = [], [], [], [], set()
    for offset in range((today - first).days):
        day = first + timedelta(days=offset)
        url = BASE_URL + 'scoreboard?dates=' + day.strftime('%Y%m%d')
        payload = json.loads(transport(url))
        fetched = datetime.now(timezone.utc)
        if not isinstance(payload.get('events'), list):
            raise ValueError('Current scoreboard is missing events')
        sources.append(dict(url=url, retrieved_at=fetched.isoformat(), raw=payload))
        for event in payload['events']:
            if timestamp(event['date']).astimezone(EASTERN).date() != day:
                raise ValueError('Scoreboard event outside the requested date')
            if event['id'] in seen:
                raise ValueError('Duplicate current-season event')
            seen.add(event['id'])
            competition = event['competitions'][0]
            status = competition['status']['type']
            notes = ' '.join(row.get('headline', '') for row in competition.get('notes', []))
            if (event['season']['type'] != 2 or status.get('name') in {'STATUS_POSTPONED', 'STATUS_CANCELED'}
                    or competition.get('type', {}).get('abbreviation') == 'ALLSTAR'
                    or ('championship' in notes.lower() and
                        any(word in notes.lower() for word in ('cup', 'in-season tournament')))):
                excluded.append(event['id'])
                continue
            if not status.get('completed'):
                if timestamp(event['date']) + timedelta(hours=48) < now:
                    raise ValueError('Unresolved older game in current-season results')
                pending.append(event['id'])
                continue
            game = parse_result(dict(header=event), event['id'])
            if timestamp(game['scheduled_start']) != timestamp(event['date']):
                raise ValueError('Result start differs from scoreboard')
            if game['season'] != f'{year}-{str(year + 1)[-2:]}':
                raise ValueError('Unexpected season in live results')
            games.append(game)
    validate_games(games)
    return dict(games=games, sources=sources, season=f'{year}-{str(year + 1)[-2:]}',
                coverage_start=first.isoformat(), coverage_end=(today - timedelta(days=1)).isoformat(),
                excluded=excluded, pending=pending,
                collected_at=datetime.now(timezone.utc).isoformat())


def forecast_games(history, upcoming, parameters, now):
    """Replay delayed historical updates, then forecast from available results."""
    games = history['games']
    validate_games(games)
    if any(game['season'] != history['season'] for game in games):
        raise ValueError('Current-season reset history contains another season')
    if timestamp(history['collected_at']) > now or now - timestamp(history['collected_at']) > timedelta(hours=6):
        raise ValueError('Current results collection is future-dated or stale')
    expected = predict_elo(games, parameters)
    ratings, counts, latest = {}, Counter(), {}
    initial = parameters['initial_rating']
    for game in games:
        available = timestamp(game['scheduled_start']) + timedelta(hours=24)
        if available > now:
            continue
        change = parameters['k'] * (int(game['home_win']) - expected[game['game_id']]['home_probability'])
        for side, adjustment in (('home', change), ('away', -change)):
            team = game[side]['team_id']
            ratings[team] = ratings.get(team, initial) + adjustment
            counts[team] += 1
            latest[team] = max(latest.get(team, available), available)
    rows, excluded, seen = [], [], set()
    history_ids = {game['game_id'] for game in games}
    for game in upcoming:
        key = game['game_id']
        if key in seen or key in history_ids:
            raise ValueError('Duplicate upcoming game or overlap with historical outcomes')
        seen.add(key)
        start = timestamp(game['scheduled_start'])
        cutoff = start - timedelta(hours=1)
        home, away = game['home_team_id'], game['away_team_id']
        reason = None
        if home == away:
            raise ValueError('Upcoming teams must be distinct')
        if now > cutoff:
            reason = 'One-hour prediction cutoff passed'
        elif (start.astimezone(EASTERN).date() - now.astimezone(EASTERN).date()).days not in (0, 1):
            reason = 'Forecast horizon exceeds tomorrow'
        elif any(counts[team] < 5 for team in (home, away)):
            reason = 'Fewer than five available current-season results for a team'
        elif any(now - latest[team] > timedelta(days=10) for team in (home, away)):
            reason = 'Team results are too old for current inference'
        if reason:
            excluded.append(dict(game_id=key, reason=reason))
            continue
        advantage = parameters['home_advantage'] if game.get('neutral_site') is False else 0
        probability = 1 / (1 + 10 ** ((ratings[away] - ratings[home] - advantage) / parameters['rating_scale']))
        rows.append(dict(game, cutoff_at=cutoff.isoformat(), generated_at=now.isoformat(),
                         inputs_as_of=now.isoformat(), home_probability=probability,
                         home_rating=ratings[home], away_rating=ratings[away],
                         home_history_games=counts[home], away_history_games=counts[away]))
    return dict(model_version=MODEL_VERSION, generated_at=now.isoformat(), predictions=rows,
                excluded=excluded, parameters=parameters, history_season=history['season'],
                limitations=['Ratings reset to 1500 each October; this is a separate live Elo baseline.',
                             'Fixed parameters come from the existing March 2024 validation fold.',
                             'Results are eligible 24 hours after scheduled start and only after collection.',
                             'No player, injury, roster, or synergy inputs; no live performance claim.'])


def frozen_parameters():
    plan = json.loads((PROJECT_ROOT / 'config/prediction-holdout.json').read_text())
    relative = 'artifacts/predictions/elo/report.json'
    raw = (PROJECT_ROOT / relative).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != plan['frozen_files'][relative]:
        raise ValueError('Frozen Elo parameter artifact changed')
    parameters = next(row['selected_parameters'] for row in json.loads(raw)['folds']
                      if row['name'] == plan['checkpoint_fold'])
    return parameters, digest


def write_new(path, payload):
    raw = json.dumps(payload, indent=2, allow_nan=False) + '\n'
    with path.open('x') as handle:
        handle.write(raw)
    return hashlib.sha256(raw.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matches', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/live-elo')
    parser.add_argument('--daily-output', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/daily')
    args = parser.parse_args()
    try:
        parameters, parameter_hash = frozen_parameters()
        match_raw = args.matches.read_bytes()
        matches = json.loads(match_raw)
        # Validate the match report before making any source requests.
        build_daily(matches, datetime.now(EASTERN).date().isoformat(), datetime.now(timezone.utc))
        history = collect_current_results(datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        upcoming = []
        for source in matches['schedule_sources']:
            games, rejected = parse_schedule(source['raw'])
            if rejected:
                raise ValueError('Invalid upcoming schedule')
            upcoming.extend(games)
        forecasts = forecast_games(history, upcoming, parameters, now)
        run = args.output / now.strftime('%Y%m%dT%H%M%S%fZ')
        run.mkdir(parents=True, exist_ok=False)
        history_hash = write_new(run / 'history.json', history)
        forecasts['sources'] = dict(history_sha256=history_hash, frozen_elo_report_sha256=parameter_hash,
                                    matches_sha256=hashlib.sha256(match_raw).hexdigest())
        forecast_hash = write_new(run / 'forecasts.json', forecasts)
        report = build_daily(matches, now.astimezone(EASTERN).date().isoformat(), now, forecasts)
        report['sources'] = dict(matches_sha256=hashlib.sha256(match_raw).hexdigest(),
                                 forecasts_sha256=forecast_hash, history_sha256=history_hash,
                                 frozen_elo_report_sha256=parameter_hash)
        path = save_daily(args.daily_output, report)
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as error:
        parser.exit(2, f'Live Elo inference failed: {error}\n')
    print(f"Generated {len(forecasts['predictions'])} live Elo forecasts; excluded {len(forecasts['excluded'])}.\n"
          f"Saved {path}\nInputs and forecasts: {run}")


if __name__ == '__main__':
    main()
