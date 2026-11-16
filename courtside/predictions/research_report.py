"""Summarize frozen research and the already-inspected January market subset."""

import argparse
import csv
import json
from pathlib import Path
import platform

import numpy
import scipy
import sklearn

from .baseline import scores
from .holdout import paired_interval
from .history import timestamp
from .improve import DEFAULT_OUTPUT, ROOT, digest, save_new


def compare_market(development, market):
    by_id = {r['game_id']: r for r in development}
    if len(by_id) != len(development):
        raise ValueError('Duplicate development prediction')
    joined = []
    for old in market['predictions']:
        row = by_id.get(old['game_id'])
        if row is None or row['target'] != old['target'] or timestamp(row['cutoff_at']) != timestamp(old['cutoff_at']):
            raise ValueError('Market comparison must retain every matched game and its exact cutoff')
        ages = [timestamp(old['cutoff_at']) - timestamp(old[side + '_quote']['quoted_at']) for side in ('home', 'away')]
        if any(not 0 <= age.total_seconds() <= 6*3600 for age in ages):
            raise ValueError('Quote outside cutoff freshness window')
        joined.append(dict(game_id=row['game_id'], cutoff_at=row['cutoff_at'], target=row['target'],
            model_probability=row['model_probability'], market_probability=old['market_probability'],
            elo_probability=row['elo_probability'], reset_elo_probability=row['reset_elo_probability']))
    labels = [r['target'] for r in joined]
    if len({r['game_id'] for r in joined}) != len(joined):
        raise ValueError('Duplicate market game')
    return dict(games=len(joined), interpretation='Development diagnostic, not a fresh holdout; all model predictions are out of time.',
        scores={key: scores(labels, [r[key] for r in joined]) for key in
                ('model_probability', 'market_probability', 'elo_probability', 'reset_elo_probability')},
        model_minus_market_brier_95_interval=paired_interval(joined, 'model_probability', 'market_probability', 42),
        higher_probability_on_actual_winner=sum(
            (r['model_probability'] - r['market_probability']) * (2*r['target']-1) > 0 for r in joined),
        predictions=joined)


def report(output):
    selection = json.loads((output / 'selection.json').read_text())
    development = json.loads((output / 'development-summary.json').read_text())
    test = json.loads((output / 'test-report.json').read_text())
    predictions = json.loads((output / 'development-predictions.json').read_text())
    market_path = ROOT / 'artifacts/predictions/fanduel-2026-01/report-20261115T230309318315Z.json'
    market = compare_market(predictions, json.loads(market_path.read_text()))
    market['market_source_sha256'] = digest(market_path)
    market['selection_sha256'] = digest(output / 'selection.json')
    save_new(output / 'environment.json', dict(python=platform.python_version(), numpy=numpy.__version__,
        scipy=scipy.__version__, sklearn=sklearn.__version__, report_code_sha256=digest(Path(__file__))))
    save_new(output / 'development-market.json', market)
    lines = ['# Local model research results', '',
        f"Compared {len(selection['trials'])} candidates across four chronological validation periods, "
        f"using {development['scores']['model_probability']['games']:,} out-of-time validation predictions.", '',
        'Selected on validation log loss, then froze the model before collecting the final period.', '',
        f"Selected model: `{selection['selected']['spec']['name']}`.", '',
        '## Reserved final test: February 1–April 12, 2026', '',
        f"{test['expected_games']} regular-season games; {len(test['excluded'])} excluded.", '',
        '| Model | Winner accuracy | Brier (lower is better) | Log loss (lower is better) |',
        '| --- | ---: | ---: | ---: |']
    for label, key in [('New margin-strength blend', 'model_probability'), ('Previous multi-season Elo', 'elo_probability'),
                       ('Current season-reset Elo', 'reset_elo_probability')]:
        s = test['scores'][key]
        lines.append(f"| {label} | {s['accuracy']:.1%} ({round(s['accuracy']*s['games'])}/{s['games']}) | {s['brier']:.4f} | {s['log_loss']:.4f} |")
    lines += ['', f"Paired date-block 95% interval for new-model minus multi-season Elo Brier: {test['brier_difference_95_interval']}.", '',
        '## January FanDuel development diagnostic', '',
        'These 141 matched games were already inspected before this experiment. Predictions below come from the chronological validation fold trained before November 2025, not the final checkpoint trained through January. This subset is not fresh proof of beating the market.', '',
        '| Model | Winner accuracy | Brier | Log loss |', '| --- | ---: | ---: | ---: |']
    for label, key in [('New blend', 'model_probability'), ('FanDuel (margin removed)', 'market_probability'), ('Previous Elo', 'elo_probability')]:
        s = market['scores'][key]
        lines.append(f"| {label} | {s['accuracy']:.1%} | {s['brier']:.4f} | {s['log_loss']:.4f} |")
    lines += ['', f"Model assigned more probability than FanDuel to the actual winner in {market['higher_probability_on_actual_winner']}/{market['games']} games. This is not a return/profit statistic.", '',
        '## Scope and limitations', '',
        '- Team strength, score margins, opponents, venue and rest. No verified injuries, lineups or player inputs.',
        '- Results become usable 24 hours after scheduled start; that is an assumption, not verified historical publication time.',
        '- Final model weights stayed fixed. Ratings could update from earlier test games only after their availability delay.',
        '- One reserved period; this does not establish a universal best model or a guaranteed 70% win rate.',
        '- No fresh final-period sportsbook comparison. January odds cover only 141 of 233 games.',
        '- Local artifacts only. Existing UI/default model and production deployment were not replaced.', '']
    with (output / 'report.md').open('x') as handle:
        handle.write('\n'.join(lines))
    with (output / 'test-predictions.csv').open('x', newline='') as handle:
        columns = ['game_id', 'scheduled_start', 'cutoff_at', 'target', 'model_probability', 'elo_probability', 'reset_elo_probability']
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(test['predictions'])
    print('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT.with_name('improved-v2'))
    report(parser.parse_args().output)
