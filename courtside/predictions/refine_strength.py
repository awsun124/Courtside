"""One bounded refinement of the development winner, before the final test.

Retains the complete first search and its frozen selection. Refines regularization
and probability scale; never reads final-test outcomes or market prices.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from .baseline import scores
from .history import timestamp
from .improve import (DEFAULT_OUTPUT, boundary, digest, fit, load_development,
                      partition, predict, save_new, summarize)


def refine(source, output):
    plan = json.loads((source / 'plan.json').read_text())
    old = json.loads((source / 'selection.json').read_text())
    rows = json.loads((source / 'development-features.json').read_text())
    _, hashes = load_development()
    if hashes != old['source_hashes']:
        raise ValueError('Development sources changed')
    folds = [partition(rows, *dates) for dates in plan['folds']]
    validation_rows = [dict(r, fold=i) for i, (_, validation) in enumerate(folds) for r in validation]
    labels = [r['target'] for r in validation_rows]
    previous_rows = json.loads((source / 'development-predictions.json').read_text())
    if [r['game_id'] for r in previous_rows] != [r['game_id'] for r in validation_rows]:
        raise ValueError('Development partitions changed')
    values = {old['selected']['spec']['name']: np.array([r['model_probability'] for r in previous_rows])}
    compact = next(t['spec']['columns'] for t in old['trials'] if t['spec']['name'] == 'ridge-compact-100-12')
    full = list(rows[0]['features'])
    specs = []
    for group, columns, alphas in [('compact', compact, (30, 300)), ('full', full, (300, 1000))]:
        for alpha in alphas:
            for sigma in (12, 13.5, 15):
                spec = dict(name=f'ridge-{group}-{alpha}-{sigma}', kind='ridge', columns=columns, alpha=alpha, sigma=sigma)
                if spec['name'] not in {t['spec']['name'] for t in old['trials']}:
                    specs.append(spec)
    for c in (.003, .03):
        specs.append(dict(name=f'logistic-full-{c}', kind='logistic', columns=full, C=c))
    for rate in (.03, .06, .1):
        specs.append(dict(name=f'normal-margin_{rate}-13.5', kind='normal', columns=[f'margin_{rate}'], sigma=13.5))
    trials = list(old['trials'])
    new_trials = []
    for spec in specs:
        probabilities, fold_metrics = [], []
        for train, validation in folds:
            p = predict(validation, spec, fit(train, spec))
            probabilities.extend(p.tolist())
            fold_metrics.append(scores([r['target'] for r in validation], p))
        values[spec['name']] = np.array(probabilities)
        trial = dict(spec=spec, validation=scores(labels, probabilities), folds=fold_metrics)
        new_trials.append(trial)
        print(spec['name'], trial['validation'], flush=True)
    for kind in ('normal', 'logistic', 'ridge'):
        option = min((t for t in new_trials if t['spec']['kind'] == kind), key=lambda t: t['validation']['log_loss'])
        for weight in (.25, .5, .75):
            members = [old['selected']['spec'], option['spec']]
            spec = dict(name=f"refined-blend-{option['spec']['name']}-{weight}", kind='blend', members=members, weights=[1-weight, weight])
            p = (1-weight)*values[members[0]['name']] + weight*values[members[1]['name']]
            values[spec['name']] = p
            offset, fold_metrics = 0, []
            for _, validation in folds:
                fold_metrics.append(scores([r['target'] for r in validation], p[offset:offset+len(validation)]))
                offset += len(validation)
            new_trials.append(dict(spec=spec, validation=scores(labels, p), folds=fold_metrics))
    trials.extend(new_trials)
    if len(trials) > plan['max_candidate_models']:
        raise ValueError('Exceeded total search budget including the first round')
    selected = min(trials, key=lambda t: t['validation']['log_loss'])
    # Older nonwinning candidates cannot become the winner without changed metrics.
    p = values[selected['spec']['name']]
    training = [r for r in rows if r['eligible'] and timestamp(r['scheduled_start']) + timedelta(hours=24)
                <= boundary(plan['final_test_start'])]
    if output.exists():
        raise ValueError('Refinement output must be new')
    output.mkdir(parents=True)
    save_new(output / 'plan.json', plan)
    model = fit(training, selected['spec'])
    joblib.dump(model, output / 'model.joblib')
    selection = dict(old, selected=selected, trials=sorted(trials, key=lambda t: t['validation']['log_loss']),
        created_at=datetime.now(timezone.utc).isoformat(), parent_selection_sha256=digest(source / 'selection.json'),
        model_sha256=digest(output / 'model.joblib'), plan_sha256=digest(output / 'plan.json'),
        code_hashes={**old['code_hashes'], Path(__file__).name: digest(Path(__file__))})
    save_new(output / 'selection.json', selection)
    predictions = [dict(r, model_probability=float(prob)) for r, prob in zip(validation_rows, p)]
    save_new(output / 'development-predictions.json', predictions)
    save_new(output / 'development-summary.json', summarize(predictions))
    print('FINAL FROZEN SELECTION', selected, 'TOTAL TRIALS', len(trials), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT.with_name('improved-v2'))
    args = parser.parse_args()
    with threadpool_limits(limits=1):
        refine(args.source, args.output)
