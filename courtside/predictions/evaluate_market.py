"""Evaluate saved prospective forecasts against a preselected bookmaker."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .baseline import scores
from .history import BASE_URL, fetch_bytes, parse_result, timestamp
from .odds import PROJECT_ROOT
from .service import read_predictions
from .odds_matching import TEAM_IDS


def compare(games, results, bookmaker='fanduel'):
    settled, pending, excluded, seen = [], [], [], set()
    for game in games:
        key = game['game_id']
        if key in seen:
            raise ValueError('Duplicate saved game')
        seen.add(key)
        if game['model_status'] != 'available':
            excluded.append(dict(game_id=key, reason='No on-time model forecast'))
            continue
        books = [book for book in game['bookmakers'] if book['bookmaker'] == bookmaker
                 and book['benchmark_eligible_at_save']]
        if len(books) != 1:
            excluded.append(dict(game_id=key, reason='Missing or ambiguous eligible bookmaker quote'))
            continue
        result = results.get(key)
        if result is None:
            pending.append(key)
            continue
        if any('espn:' + TEAM_IDS.get(game[side + '_team'], '') != result[side]['team_id']
               for side in ('home', 'away')):
            raise ValueError('Outcome matchup differs from forecast')
        if timestamp(result['scheduled_start']) != timestamp(game['scheduled_start']):
            excluded.append(dict(game_id=key, reason='Final game tipoff differs from saved schedule'))
            continue
        quote = books[0]
        model, market, outcome = game['home_probability'], quote['home_market_probability'], int(result['home_win'])
        # Positive difference: home undervalued. Negative: away undervalued.
        value_home = model > market if model != market else None
        settled.append(dict(game_id=key, target=outcome, model_probability=model,
            market_probability=market, saved_at=game['saved_at'], quote_at=quote['available_at'],
            model_generated_at=game['forecast_generated_at'], model_version=game['model_version'],
            undervalued_side='home' if value_home is True else 'away' if value_home is False else None,
            undervalued_pick_won=(bool(outcome) == value_home) if value_home is not None else None))
    eligible = [row for row in settled if row['undervalued_pick_won'] is not None]
    wins = sum(row['undervalued_pick_won'] for row in eligible)
    model_scores = scores([r['target'] for r in settled], [r['model_probability'] for r in settled]) if settled else None
    market_scores = scores([r['target'] for r in settled], [r['market_probability'] for r in settled]) if settled else None
    return dict(bookmaker=bookmaker, settled_games=len(settled), pending_game_ids=pending, excluded=excluded,
                model=model_scores, market=market_scores,
                undervalued_picks=dict(games=len(eligible), wins=wins, win_rate=wins / len(eligible) if eligible else None),
                predictions=settled,
                limitations=['Only saved prospective forecasts with eligible paired quotes are scored.',
                             'Observations were saved before the one-hour deadline; quote and forecast times may differ.',
                             'Undervalued-pick win rate is not proof of profitability or better probability forecasts.',
                             'Historical 2025 model tests lack matching odds; they are not included here.',
                             'No statistical improvement claim is made from this small live sample.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bookmaker', default='fanduel')
    parser.add_argument('--daily-folder', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/daily')
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/market-evaluation')
    args = parser.parse_args()
    try:
        now = datetime.now(timezone.utc)
        games, results, sources = [], {}, []
        for folder in sorted(args.daily_folder.iterdir()):
            if not folder.is_dir():
                continue
            saved = read_predictions(args.daily_folder, folder.name, now)
            games.extend(saved['games'])
            url = BASE_URL + 'scoreboard?dates=' + folder.name.replace('-', '')
            raw = fetch_bytes(url)
            payload = json.loads(raw)
            if not isinstance(payload.get('events'), list):
                raise ValueError('Outcome scoreboard is missing events')
            sources.append(dict(url=url, retrieved_at=datetime.now(timezone.utc).isoformat(),
                                sha256=hashlib.sha256(raw).hexdigest(), raw=payload))
            for event in payload['events']:
                if event['competitions'][0]['status']['type'].get('completed') is True:
                    result = parse_result(dict(header=event), event['id'])
                    if result['game_id'] in results:
                        raise ValueError('Duplicate final result')
                    results[result['game_id']] = result
        report = compare(games, results, args.bookmaker)
        report.update(evaluated_at=now.isoformat(), outcome_sources=sources, saved_games=len(games))
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / (now.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x') as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write('\n')
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'Market evaluation failed: {error}\n')
    print(f"{args.bookmaker}: {report['settled_games']} settled, {len(report['pending_game_ids'])} pending, "
          f"{len(report['excluded'])} excluded.\nSaved {path}")
    if not report['settled_games']:
        print('Accuracy, Brier, log loss and undervalued-pick win rate are unavailable until eligible games finish.')
    else:
        print(json.dumps({key: report[key] for key in ('model', 'market', 'undervalued_picks')}, indent=2))


if __name__ == '__main__':
    main()
