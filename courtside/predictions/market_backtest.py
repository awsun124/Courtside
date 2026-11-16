"""Complete fixed-period FanDuel coverage audit and season-reset Elo backtest."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .baseline import scores, calibration
from .elo import predict_elo
from .history import EASTERN, timestamp
from .live_elo import frozen_parameters
from .odds import PROJECT_ROOT, atomic_json
from .oddspapi_pilot import HistoricalRateLimit, cutoff_price, fetch_history, load_key
from .odds_matching import TEAM_IDS


def read_checked(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def collect(folder):
    plan, _ = read_checked(folder / 'plan.json')
    fixtures, _ = read_checked(folder / 'fixtures.json')
    if len(fixtures) > 500 or len({row['fixtureId'] for row in fixtures}) != len(fixtures):
        raise ValueError('Invalid fixture inventory')
    for row in fixtures:
        if (row['tournamentId'] != 132 or row['sportId'] != 11 or
                not re.fullmatch(r'id[0-9]+', row['fixtureId'])):
            raise ValueError('Invalid NBA fixture')
    destination = folder / 'quotes'
    destination.mkdir(exist_ok=True)
    key = load_key()
    previous, counts = None, Counter()
    for index, fixture in enumerate(fixtures, 1):
        path = destination / (fixture['fixtureId'] + '.json')
        if path.exists():
            cached, _ = read_checked(path)
            if cached['fixture_id'] != fixture['fixtureId'] or cached['bookmaker'] != plan['bookmaker']:
                raise ValueError('Cached quote identity mismatch')
            counts[cached['status']] += 1
            continue
        if previous is not None:
            time.sleep(max(0, 5.1 - (time.monotonic() - previous)))
        try:
            for attempt in range(3):
                try:
                    payload = fetch_history(key, fixture['fixtureId'], None, plan['bookmaker'])
                    break
                except HistoricalRateLimit as error:
                    if attempt == 2:
                        raise
                    print(f'Provider cooldown; waiting {error.seconds:g}s before retry {attempt+1}/2.', flush=True)
                    time.sleep(error.seconds)
            previous = time.monotonic()
            status = 'not_found' if payload is None else 'received'
            market = payload.get('bookmakers', {}).get(plan['bookmaker'], {}).get('markets', {}).get('111') if payload else None
            saved = dict(fixture_id=fixture['fixtureId'], bookmaker=plan['bookmaker'], status=status,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                response_sha256=payload.get('_response_sha256') if payload else None,
                retained_market='111',
                data=dict(fixtureId=fixture['fixtureId'], bookmakers={plan['bookmaker']: dict(markets={'111': market})})
                     if market else None)
            atomic_json(path, saved)
            counts[status] += 1
        except ValueError as error:
            print(f"Stopped at {index}/{len(fixtures)}: {error}. Successful quote caches retained.", flush=True)
            raise
        if index % 5 == 0 or index == len(fixtures):
            print(f"Coverage {index}/{len(fixtures)}: {dict(counts)}", flush=True)
    return counts


def uncertainty(rows, seed=42, resamples=2000):
    if not rows:
        return None
    import numpy as np
    by_date = defaultdict(list)
    for row in rows:
        by_date[row['date']].append((row['model_probability'] - row['target']) ** 2 -
                                    (row['market_probability'] - row['target']) ** 2)
    blocks = list(by_date.values())
    rng = np.random.default_rng(seed)
    differences = []
    for _ in range(resamples):
        sample = [value for index in rng.integers(0, len(blocks), size=len(blocks)) for value in blocks[index]]
        differences.append(sum(sample) / len(sample))
    return dict(method='Paired resampling of Eastern game dates', dates=len(blocks), resamples=resamples,
                model_minus_market_brier_mean=float(np.mean([v for b in blocks for v in b])),
                model_minus_market_brier_95_interval=[float(v) for v in np.quantile(differences, [0.025, 0.975])])


def evaluate(games, fixtures, quotes, plan, parameters):
    predictions = predict_elo(games, parameters)
    selected = [game for game in games if plan['test_start'] <=
                timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat() <= plan['test_end']]
    rows, excluded, used = [], [], set()
    for game in selected:
        key = game['game_id']
        cutoff = timestamp(predictions[key]['cutoff_at'])
        sides = {side: game[side]['team_id'] for side in ('home', 'away')}
        histories = {side: [g for g in games if timestamp(g['scheduled_start']) + timedelta(hours=24) <= cutoff
                           and g['season'] == game['season']
                           and sides[side] in (g['home']['team_id'], g['away']['team_id'])]
                     for side in sides}
        reason = None
        if any(len(histories[side]) < plan['minimum_history'] for side in sides):
            reason = 'Insufficient available pregame history'
        elif any(cutoff - max(timestamp(g['scheduled_start']) + timedelta(hours=24) for g in histories[side]) >
                 timedelta(days=10) for side in sides):
            reason = 'Stale team history'
        candidates = [fixture for fixture in fixtures
                      if 'espn:' + TEAM_IDS.get(fixture['participant1Name'], '') == sides['home']
                      and 'espn:' + TEAM_IDS.get(fixture['participant2Name'], '') == sides['away']
                      and abs(timestamp(fixture['startTime']) - timestamp(game['scheduled_start'])) <= timedelta(minutes=15)]
        if reason is None and len(candidates) != 1:
            reason = 'No unique provider fixture match'
        fixture = candidates[0] if len(candidates) == 1 else None
        if fixture:
            if fixture['fixtureId'] in used:
                raise ValueError('Provider fixture matched more than once')
            used.add(fixture['fixtureId'])
        if reason:
            excluded.append(dict(game_id=key, reason=reason))
            continue
        saved = quotes[fixture['fixtureId']]
        prices = [cutoff_price(saved['data'], plan['bookmaker'], outcome, cutoff) for outcome in (111, 112)]
        if any(price is None for price in prices):
            excluded.append(dict(game_id=key, fixture_id=fixture['fixtureId'], reason='Missing eligible two-sided cutoff odds'))
            continue
        raw_home, raw_away = (1 / price['price'] for price in prices)
        market = raw_home / (raw_home + raw_away)
        model, target = predictions[key]['home_probability'], int(game['home_win'])
        value_home = model > market if model != market else None
        rows.append(dict(game_id=key, fixture_id=fixture['fixtureId'],
            date=timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat(),
            home_team=game['home']['name'], away_team=game['away']['name'],
            scheduled_start=game['scheduled_start'], cutoff_at=cutoff.isoformat(), target=target,
            model_probability=model, market_probability=market, home_quote=prices[0], away_quote=prices[1],
            home_history_games=len(histories['home']), away_history_games=len(histories['away']),
            undervalued_side='home' if value_home is True else 'away' if value_home is False else None,
            undervalued_pick_won=(bool(target) == value_home) if value_home is not None else None,
            market_probability_of_value_pick=market if value_home else 1-market if value_home is False else None))
    if len(rows) + len(excluded) != len(selected):
        raise ValueError('Game coverage accounting failed')
    picks = [r for r in rows if r['undervalued_side']]
    wins = sum(r['undervalued_pick_won'] for r in picks)
    interval = None
    if picks:
        n, p, z = len(picks), wins / len(picks), 1.959963984540054
        center = (p + z*z/(2*n)) / (1 + z*z/n)
        half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
        interval = [max(0, center-half), min(1, center+half)]
    labels = [r['target'] for r in rows]
    mean = lambda values: sum(values) / len(values) if values else None
    return dict(expected_espn_games=len(selected), matched_games=len(rows), excluded=excluded,
        unmatched_provider_fixtures=[f['fixtureId'] for f in fixtures if f['fixtureId'] not in used],
        model_scores=scores(labels, [r['model_probability'] for r in rows]) if rows else None,
        market_scores=scores(labels, [r['market_probability'] for r in rows]) if rows else None,
        undervalued_picks=dict(games=len(picks), wins=wins, win_rate=wins/len(picks) if picks else None,
                              win_rate_wilson_95_interval=interval,
                              average_market_probability=mean([r['market_probability_of_value_pick'] for r in picks])),
        model_brier_better_games=sum((r['model_probability']-r['target'])**2 < (r['market_probability']-r['target'])**2 for r in rows),
        calibration={name: calibration(labels, [r[column] for r in rows]) for name, column in
                     (('model','model_probability'), ('market','market_probability'))},
        uncertainty=uncertainty(rows, plan['bootstrap_seed'], plan['bootstrap_resamples']), predictions=rows)


def score(folder):
    plan, plan_hash = read_checked(folder / 'plan.json')
    if (plan['result_delay_hours'] != 24 or plan['cutoff_minutes'] != 60 or
            plan['maximum_quote_age_hours'] != 6 or plan['model'] != 'elo-current-season-reset-v1'):
        raise ValueError('Plan timing/model policy does not match the implemented fixed evaluator')
    fixtures, fixture_hash = read_checked(folder / 'fixtures.json')
    history_path = folder / 'results' / f"results-{plan['history_start']}-{plan['test_end']}.json"
    history, history_hash = read_checked(history_path)
    if history['coverage']['complete'] is not True:
        raise ValueError('ESPN source coverage is incomplete; repair source errors before scoring')
    parameters, parameter_hash = frozen_parameters()
    quotes, quote_hashes = {}, {}
    for fixture in fixtures:
        data, digest = read_checked(folder / 'quotes' / (fixture['fixtureId'] + '.json'))
        if data['fixture_id'] != fixture['fixtureId'] or data['bookmaker'] != plan['bookmaker']:
            raise ValueError('Quote cache identity mismatch')
        quotes[fixture['fixtureId']], quote_hashes[fixture['fixtureId']] = data, digest
    report = evaluate(history['games'], fixtures, quotes, plan, parameters)
    secondary_path = folder / 'secondary-plan.json'
    if secondary_path.exists():
        secondary_plan, secondary_plan_hash = read_checked(secondary_path)
        previous, previous_hash = read_checked(folder / 'carryover-history.json')
        if (previous_hash != secondary_plan['history_sha256'] or previous['coverage']['complete'] is not True
                or any(timestamp(g['scheduled_start']).astimezone(EASTERN).date().isoformat() >= plan['history_start']
                       for g in previous['games'])):
            raise ValueError('Secondary carryover history provenance mismatch')
        secondary = evaluate(previous['games'] + history['games'], fixtures, quotes, plan, parameters)
        if {r['game_id'] for r in secondary['predictions']} != {r['game_id'] for r in report['predictions']}:
            raise ValueError('Secondary model does not use the identical eligible game set')
        report['secondary_carryover_elo'] = secondary
        report['secondary_source_hashes'] = dict(plan=secondary_plan_hash, history=previous_hash)
    report['exclusion_counts'] = dict(Counter(row['reason'] for row in report['excluded']))
    expected_dates = Counter(timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat()
                             for game in history['games'] if plan['test_start'] <=
                             timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat() <= plan['test_end'])
    matched_dates = Counter(row['date'] for row in report['predictions'])
    report['coverage_by_date'] = [dict(date=day, expected=count, matched=matched_dates[day],
                                      excluded=count-matched_dates[day]) for day,count in sorted(expected_dates.items())]
    report['model_brier_better_game_rate'] = report['model_brier_better_games']/report['matched_games'] if report['matched_games'] else None
    report['code_sha256'] = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                             for name in ('market_backtest.py','oddspapi_pilot.py','elo.py','features.py','history.py')}
    report.update(plan=plan, parameters=parameters, source_hashes=dict(plan=plan_hash, fixtures=fixture_hash,
        history=history_hash, frozen_elo_report=parameter_hash, quotes=quote_hashes),
        evaluated_at=datetime.now(timezone.utc).isoformat(), provider_fixtures=len(fixtures),
        quote_statuses=dict(Counter(q['status'] for q in quotes.values())),
        limitations=['Frozen-parameter retrospective comparison; not an untouched preregistered holdout.',
                     'Current-season-reset Elo only; this does not evaluate player or synergy checkpoints.',
                     'Historical result availability is assumed to be 24 hours after scheduled start.',
                     'Quotes are latest states at cutoff, excluded if suspended or older than six hours.',
                     'Performance applies to the common odds-covered subset; exclusions may bias coverage.',
                     'No parameters, books, periods or pick thresholds were tuned against this report.'])
    output = folder / ('report-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with output.open('x') as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write('\n')
    lines = ['# January 2026: season-reset Elo versus FanDuel', '',
        f"Period: {plan['test_start']} through {plan['test_end']} (Eastern game dates).",
        f"Coverage: {report['matched_games']} matched of {report['expected_espn_games']} completed regular-season games; "
        f"{len(report['excluded'])} excluded. Provider inventory: {len(fixtures)} fixtures.", '',
        '## Performance on identical eligible games', '',
        '| Model | Games | Winner accuracy | Brier score | Log loss |',
        '| --- | ---: | ---: | ---: | ---: |']
    for label, field in (('Season-reset Elo','model_scores'),('FanDuel margin-adjusted','market_scores')):
        values = report[field]
        if values:
            lines.append(f"| {label} | {values['games']} | {values['accuracy']:.2%} | {values['brier']:.6f} | {values['log_loss']:.6f} |")
    if report.get('secondary_carryover_elo',{}).get('model_scores'):
        values=report['secondary_carryover_elo']['model_scores']
        lines.append(f"| Multi-season Elo (secondary) | {values['games']} | {values['accuracy']:.2%} | {values['brier']:.6f} | {values['log_loss']:.6f} |")
    picks = report['undervalued_picks']
    lines.extend(['', '## Relatively undervalued side', '',
        f"Won {picks['wins']} of {picks['games']} games. This selects the side Elo rates higher than the margin-adjusted market; "
        'it is not necessarily the predicted winner or a profitable wager.'])
    if picks['games']:
        lines.append(f"Win rate: {picks['win_rate']:.2%}; Wilson 95% interval: "
                     f"{picks['win_rate_wilson_95_interval'][0]:.2%} to {picks['win_rate_wilson_95_interval'][1]:.2%}. "
                     f"Mean market probability for these sides: {picks['average_market_probability']:.2%}.")
    if report['matched_games']:
        lines.append(f"Elo assigned more probability to the actual winner (and had lower per-game Brier loss) in "
                     f"{report['model_brier_better_games']}/{report['matched_games']} games "
                     f"({report['model_brier_better_game_rate']:.2%}). Overall mean Brier loss also depends on the size of each error.")
    if report['uncertainty']:
        u = report['uncertainty']
        lines.extend(['', '## Paired probability-score comparison', '',
            f"Mean Elo-minus-market Brier difference: {u['model_minus_market_brier_mean']:.6f}. "
            f"Date-block bootstrap 95% interval: {u['model_minus_market_brier_95_interval']}. "
            'Negative favors Elo; positive favors FanDuel.'])
    if report.get('secondary_carryover_elo'):
        secondary=report['secondary_carryover_elo']
        lines.extend(['', '## Predeclared secondary multi-season Elo', '',
            f"Undervalued side won {secondary['undervalued_picks']['wins']}/{secondary['undervalued_picks']['games']} games. "
            'This uses the same eligible games and fixed parameters, with earlier-season ratings carried forward. ' 
            'It is not a replacement selected after observing the primary result.'])
        if secondary['uncertainty']:
            lines.append('Secondary Elo-minus-FanDuel Brier 95% date-block interval: ' +
                         str(secondary['uncertainty']['model_minus_market_brier_95_interval']))
    lines.extend(['', '## Coverage exclusions', '', *[f'- {reason}: {count}' for reason,count in report['exclusion_counts'].items()],
                  '', '## Limits', '', *['- '+note for note in report['limitations']]])
    output.with_suffix('.md').write_text('\n'.join(lines)+'\n')
    with output.with_suffix('.csv').open('x',newline='') as handle:
        fields = ['game_id','date','away_team','home_team','target','model_probability','market_probability',
                  'cutoff_at','undervalued_side','undervalued_pick_won']
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(report['predictions'])
    print(json.dumps({k: report[k] for k in ('expected_espn_games','matched_games','quote_statuses',
        'model_scores','market_scores','undervalued_picks','uncertainty')}, indent=2))
    print('Report:', output)
    return report, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['collect','score'])
    parser.add_argument('--folder', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/fanduel-2026-01')
    args = parser.parse_args()
    try:
        collect(args.folder) if args.phase == 'collect' else score(args.folder)
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as error:
        parser.exit(2, f'Market backtest failed: {error}\n')


if __name__ == '__main__':
    main()
