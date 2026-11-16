"""Run the frozen research model locally; does not publish or replace daily Elo."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import joblib
from threadpoolctl import threadpool_limits

from .history import EASTERN, timestamp
from .improve import DEFAULT_OUTPUT, digest, predict, save_new
from .odds_matching import parse_schedule
from .strength import VERSION, build_strength_rows


def load_frozen(folder):
    selection = json.loads((folder / 'selection.json').read_text())
    if selection['feature_version'] != VERSION or digest(folder / 'model.joblib') != selection['model_sha256']:
        raise ValueError('Model version or artifact checksum mismatch')
    for name, expected in selection['code_hashes'].items():
        if digest(Path(__file__).with_name(name)) != expected:
            raise ValueError('Frozen implementation changed')
    return selection, joblib.load(folder / 'model.joblib')


def forecast_games(games, upcoming, selection, model, now):
    if timestamp(selection['last_training_label_available']) > now:
        raise ValueError('Model was trained on outcomes unavailable at the forecast time')
    fixtures, excluded, seen = [], [], {g['game_id'] for g in games}
    for game in upcoming:
        if game['game_id'] in seen:
            raise ValueError('Duplicate fixture or historical overlap')
        seen.add(game['game_id'])
        start = timestamp(game['scheduled_start'])
        cutoff = start - timedelta(hours=1)
        reason = None
        if now > cutoff:
            reason = 'One-hour prediction cutoff passed'
        elif (start.astimezone(EASTERN).date() - now.astimezone(EASTERN).date()).days not in (0, 1):
            reason = 'Forecast horizon exceeds tomorrow'
        if reason:
            excluded.append(dict(game_id=game['game_id'], reason=reason))
            continue
        year = start.astimezone(EASTERN).year - (start.astimezone(EASTERN).month < 10)
        fixtures.append(dict(game_id=game['game_id'], scheduled_start=start.isoformat(),
            season=f'{year}-{str(year+1)[-2:]}', home=dict(team_id=game['home_team_id']),
            away=dict(team_id=game['away_team_id']), neutral_site=game.get('neutral_site')))
    ids = {g['game_id'] for g in fixtures}
    rows = [r for r in build_strength_rows(games, fixtures, as_of=now) if r['game_id'] in ids]
    eligible = []
    for row in rows:
        reason = None
        if not row['eligible']:
            reason = 'Fewer than five available current-season results for a team'
        elif any(now - timestamp(value) > timedelta(days=10) for value in row['latest_results'].values()):
            reason = 'Team results are too old for current inference'
        if reason:
            excluded.append(dict(game_id=row['game_id'], reason=reason))
        else:
            eligible.append(row)
    probabilities = predict(eligible, selection['selected']['spec'], model) if eligible else []
    originals = {g['game_id']: g for g in upcoming}
    predictions = [dict(originals[row['game_id']], cutoff_at=row['cutoff_at'], generated_at=now.isoformat(),
        inputs_as_of=now.isoformat(), home_probability=float(p), history_counts=row['history_counts'])
        for row, p in zip(eligible, probabilities)]
    return dict(model_version=VERSION, model_name=selection['selected']['spec']['name'],
        generated_at=now.isoformat(), predictions=predictions, excluded=excluded,
        limitations=['Local research candidate; not deployed.', 'No injury, roster or player inputs.',
                     'Delayed results plus venue, opponent-adjusted margins and rest; no odds inputs.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=DEFAULT_OUTPUT.with_name('improved-v2'))
    parser.add_argument('--history', type=Path, action='append', required=True)
    parser.add_argument('--matches', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    selection, model = load_frozen(args.model)
    games, sources, latest_collection = [], {}, None
    for path in args.history:
        data = json.loads(path.read_text())
        if not data.get('coverage', {}).get('complete') and not data.get('collected_at'):
            raise ValueError('Missing verified history coverage')
        games.extend(data['games'])
        sources[str(path)] = digest(path)
        if data.get('collected_at'):
            collected = timestamp(data['collected_at'])
            latest_collection = max(latest_collection, collected) if latest_collection else collected
    now = datetime.now(timezone.utc)
    if latest_collection is None or not timedelta(0) <= now - latest_collection <= timedelta(hours=6):
        raise ValueError('A current history collection less than six hours old is required')
    matches = json.loads(args.matches.read_text())
    upcoming = []
    for source in matches['schedule_sources']:
        rows, rejected = parse_schedule(source['raw'])
        if rejected:
            raise ValueError('Invalid schedule')
        upcoming.extend(rows)
    with threadpool_limits(limits=1):
        report = forecast_games(games, upcoming, selection, model, now)
    report['sources'] = dict(histories=sources, matches_sha256=digest(args.matches),
                             selection_sha256=digest(args.model / 'selection.json'))
    save_new(args.output, report)
    print(f"Saved {len(report['predictions'])} local forecasts to {args.output}")


if __name__ == '__main__':
    main()
