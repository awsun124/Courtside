"""Save immutable pregame prediction/market snapshots from explicit inputs."""

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from .history import EASTERN, timestamp
from .odds import MIN_INTERVAL, PROJECT_ROOT
from .odds_matching import parse_schedule


def probability(value):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value < 1:
        raise ValueError('Forecast probability must be finite and strictly between zero and one')
    return float(value)


def validate_forecasts(payload, now):
    """A producer must state model identity, creation time and input cutoff."""
    if not isinstance(payload.get('model_version'), str) or not payload['model_version'].strip():
        raise ValueError('Forecasts require a model version')
    if timestamp(payload['generated_at']) > now:
        raise ValueError('Forecast file was generated in the future')
    rows = {}
    for row in payload['predictions']:
        key = row['game_id']
        if key in rows:
            raise ValueError('Duplicate forecast game ID')
        if not isinstance(key, str) or not key.startswith('espn:') or not key[5:].isdigit():
            raise ValueError('Forecast game ID must use the ESPN namespace')
        probability(row['home_probability'])
        cutoff, generated, start = (timestamp(row[field]) for field in
                                    ('cutoff_at', 'generated_at', 'scheduled_start'))
        as_of = timestamp(row['inputs_as_of'])
        if (not as_of <= generated <= timestamp(payload['generated_at']) <= now
                or generated >= start or cutoff >= start):
            raise ValueError('Forecast has invalid pregame timing')
        rows[key] = row
    return rows


def build_daily(matches, day, now, forecasts=None):
    selected_date = date.fromisoformat(day)
    generated = timestamp(matches['generated_at'])
    if generated > now or now - generated > MIN_INTERVAL:
        raise ValueError('Match report is future-dated or older than six hours; rerun the matcher')
    forecast_rows = validate_forecasts(forecasts, now) if forecasts is not None else {}
    forecast_exclusions = {row['game_id']: row['reason'] for row in forecasts.get('excluded', [])} if forecasts else {}
    scheduled, seen = [], set()
    for source in matches['schedule_sources']:
        source_time = timestamp(source['retrieved_at'])
        if source_time > now or now - source_time > MIN_INTERVAL:
            raise ValueError('Schedule source is future-dated or stale')
        games, rejected = parse_schedule(source['raw'])
        if rejected:
            raise ValueError('Schedule contains invalid events; rerun the matcher')
        for game in games:
            if game['game_id'] in seen:
                raise ValueError('Duplicate schedule game ID')
            seen.add(game['game_id'])
            if timestamp(game['scheduled_start']).astimezone(EASTERN).date() == selected_date:
                scheduled.append(game)
    matched = {row['game_id']: row for row in matches['matched']}
    if len(matched) != len(matches['matched']):
        raise ValueError('Duplicate matched game ID')
    rows = []
    for game in sorted(scheduled, key=lambda row: (row['scheduled_start'], row['game_id'])):
        start = timestamp(game['scheduled_start'])
        if start <= now:
            continue
        cutoff = start - timedelta(hours=1)
        row = dict(game, cutoff_at=cutoff.isoformat(), model_status='unavailable',
                   model_reason=forecast_exclusions.get(game['game_id'], 'No current model forecast supplied'), home_probability=None,
                   away_probability=None, market_status='unavailable', bookmakers=[])
        forecast = forecast_rows.get(game['game_id'])
        if forecast is not None:
            if (timestamp(forecast['scheduled_start']) != start or
                    any(forecast[field] != game[field] for field in ('home_team_id', 'away_team_id')) or
                    timestamp(forecast['cutoff_at']) != cutoff):
                raise ValueError('Forecast matchup, tipoff or cutoff does not match schedule')
            if timestamp(forecast['generated_at']) > cutoff:
                row['model_reason'] = 'Forecast was generated after the one-hour cutoff'
            elif now > cutoff:
                row['model_reason'] = 'Snapshot was not saved by the one-hour cutoff'
            else:
                home = probability(forecast['home_probability'])
                row.update(model_status='available', model_reason=None,
                           model_version=forecasts['model_version'], home_probability=home,
                           away_probability=1 - home, forecast_generated_at=forecast['generated_at'],
                           inputs_as_of=forecast['inputs_as_of'])
                if forecast.get('availability') is not None:
                    row['availability']=forecast['availability']
        market = matched.get(game['game_id'])
        if market is not None:
            if any(market[field] != game[field] for field in
                   ('scheduled_start', 'home_team_id', 'away_team_id')):
                raise ValueError('Matched market does not match saved schedule')
            row['odds_game_id'] = market['odds_game_id']
            for book in market['bookmakers']:
                available = timestamp(book['available_at'])
                retrieved = timestamp(book['retrieved_at'])
                eligible = (available <= retrieved <= generated <= now <= cutoff
                            and now - available <= MIN_INTERVAL
                            and now - retrieved <= MIN_INTERVAL)
                saved = dict(book, benchmark_eligible=eligible,
                             stale=now - available > MIN_INTERVAL or now - retrieved > MIN_INTERVAL)
                saved['home_difference_percentage_points'] = (
                    100 * (row['home_probability'] - probability(book['home_market_probability']))
                    if eligible and row['model_status'] == 'available' else None)
                row['bookmakers'].append(saved)
            if any(book['benchmark_eligible'] for book in row['bookmakers']):
                row['market_status'] = 'available'
        rows.append(row)
    return dict(schema_version=1, date=day, timezone='America/New_York',
                saved_at=now.isoformat(), cutoff_minutes_before_tipoff=60,
                match_report_generated_at=matches['generated_at'], games=rows,
                limitations=['Model forecasts require a separate current-input inference producer.',
                             'Differences from market probabilities do not establish profitability.'])


def save_daily(output, report):
    """Exclusive creation prevents reruns from rewriting recorded predictions."""
    folder = Path(output) / report['date']
    folder.mkdir(parents=True, exist_ok=True)
    name = timestamp(report['saved_at']).strftime('%Y%m%dT%H%M%S%fZ') + '.json'
    path = folder / name
    raw = json.dumps(report, indent=2, allow_nan=False) + '\n'
    with path.open('x') as handle:
        handle.write(raw)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matches', type=Path, required=True)
    parser.add_argument('--forecasts', type=Path)
    parser.add_argument('--date', default=datetime.now(EASTERN).date().isoformat())
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/daily')
    args = parser.parse_args()
    try:
        now = datetime.now(timezone.utc)
        match_raw = args.matches.read_bytes()
        forecast_raw = args.forecasts.read_bytes() if args.forecasts else None
        report = build_daily(json.loads(match_raw), args.date, now,
                             json.loads(forecast_raw) if forecast_raw is not None else None)
        report['sources'] = dict(matches_sha256=hashlib.sha256(match_raw).hexdigest(),
                                 forecasts_sha256=hashlib.sha256(forecast_raw).hexdigest() if forecast_raw else None)
        path = save_daily(args.output, report)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'Daily snapshot failed: {error}\n')
    available = sum(row['model_status'] == 'available' for row in report['games'])
    print(f"Saved {len(report['games'])} upcoming games; {available} model forecasts available.\n{path}")


if __name__ == '__main__':
    main()
