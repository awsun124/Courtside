"""Bounded chronological model research. Run `python -m courtside.predictions.improve`.

Develop freezes selection and a fitted artifact. Test reads that artifact without
fitting; the final period must never be used to choose settings or a new winner.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.special import ndtr
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .baseline import calibration, scores
from .history import EASTERN, timestamp
from .holdout import paired_interval
from .strength import VERSION, build_strength_rows

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / 'courtside/artifacts/predictions/improved-v1'
PLAN_PATH = Path(__file__).with_name('research_plan.json')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def boundary(day):
    return datetime.fromisoformat(day).replace(tzinfo=EASTERN)


def load_development():
    paths = [ROOT / 'artifacts/predictions/holdout/history.json',
             ROOT / 'artifacts/predictions/fanduel-2026-01/results/results-2025-10-01-2026-01-31.json']
    games = []
    for path in paths:
        data = json.loads(path.read_text())
        if data['coverage']['complete'] is not True:
            raise ValueError('Incomplete development history')
        games.extend(data['games'])
    return games, {str(p.relative_to(ROOT)): digest(p) for p in paths}


def candidates(names):
    ratings = [n for n in names if n.startswith(('margin_', 'srs_')) and 'difference' not in n]
    groups = dict(strength=['home_court', 'elo_logit'] + ratings,
        compact=['home_court', 'elo_logit'] + ratings + ['rest_difference', 'home_back_to_back', 'away_back_to_back'],
        full=list(names))
    result = [dict(name='elo', kind='baseline', column='elo_probability'),
              dict(name='reset_elo', kind='baseline', column='reset_elo_probability')]
    for name in ratings:
        for sigma in (12, 15, 18):
            result.append(dict(name=f'normal-{name}-{sigma}', kind='normal', columns=[name], sigma=sigma))
    for group, columns in groups.items():
        for c in (.01, .1, 1, 10):
            result.append(dict(name=f'logistic-{group}-{c}', kind='logistic', columns=columns, C=c))
        for alpha in (10, 100, 1000):
            for sigma in (12, 15, 18):
                result.append(dict(name=f'ridge-{group}-{alpha}-{sigma}', kind='ridge', columns=columns,
                                   alpha=alpha, sigma=sigma))
    for group in ('compact', 'full'):
        for leaves in (3, 7):
            for iterations in (100, 250):
                result.append(dict(name=f'boost-{group}-{leaves}-{iterations}', kind='boost',
                    columns=groups[group], leaves=leaves, iterations=iterations))
    return result


def matrix(rows, spec):
    return np.asarray([[r['features'][key] for key in spec['columns']] for r in rows])


def fit(rows, spec):
    kind = spec['kind']
    if kind in ('baseline', 'normal'):
        return None
    if kind == 'blend':
        return [fit(rows, member) for member in spec['members']]
    if kind == 'logistic':
        model = make_pipeline(StandardScaler(), LogisticRegression(C=spec['C'], max_iter=2000, random_state=42))
    elif kind == 'ridge':
        model = make_pipeline(StandardScaler(), Ridge(alpha=spec['alpha']))
    elif kind == 'boost':
        model = HistGradientBoostingClassifier(learning_rate=.03, max_iter=spec['iterations'],
            max_leaf_nodes=spec['leaves'], min_samples_leaf=60, l2_regularization=10,
            early_stopping=False, random_state=42)
    else:
        raise ValueError('Unknown model kind')
    labels = [np.clip(r['margin'], -40, 40) if kind == 'ridge' else r['target'] for r in rows]
    model.fit(matrix(rows, spec), labels)
    return model


def predict(rows, spec, model):
    kind = spec['kind']
    if kind == 'baseline':
        values = [r[spec['column']] for r in rows]
    elif kind == 'normal':
        values = ndtr(matrix(rows, spec)[:, 0] / spec['sigma'])
    elif kind == 'ridge':
        values = ndtr(model.predict(matrix(rows, spec)) / spec['sigma'])
    elif kind == 'blend':
        values = sum(weight * predict(rows, member, fitted) for member, fitted, weight in
                     zip(spec['members'], model, spec['weights']))
    else:
        values = model.predict_proba(matrix(rows, spec))[:, 1]
    return np.clip(values, 1e-6, 1 - 1e-6)


def partition(rows, start, end, delay_hours=24):
    first, last = boundary(start), boundary(end)
    train = [r for r in rows if r['eligible'] and
             timestamp(r['scheduled_start']) + timedelta(hours=delay_hours) <= first]
    validation = [r for r in rows if r['eligible'] and
                  first <= timestamp(r['scheduled_start']) < last]
    if not train or not validation:
        raise ValueError('Empty chronological partition')
    return train, validation


def summarize(rows):
    labels = [r['target'] for r in rows]
    columns = ('model_probability', 'elo_probability', 'reset_elo_probability')
    months = sorted({timestamp(r['scheduled_start']).astimezone(EASTERN).strftime('%Y-%m') for r in rows})
    def in_month(row, month):
        return timestamp(row['scheduled_start']).astimezone(EASTERN).strftime('%Y-%m') == month
    return dict(scores={key: scores(labels, [r[key] for r in rows]) for key in columns},
        brier_difference_95_interval=paired_interval(rows, 'model_probability', 'elo_probability', 42),
        calibration=calibration(labels, [r['model_probability'] for r in rows]),
        months={month: {key: scores([r['target'] for r in rows if in_month(r, month)],
            [r[key] for r in rows if in_month(r, month)]) for key in columns} for month in months})


def development(output):
    plan = json.loads(PLAN_PATH.read_text())
    if (output / 'selection.json').exists():
        raise ValueError('Selection already frozen; use another output for a new development run')
    games, sources = load_development()
    if any(timestamp(g['scheduled_start']) >= boundary(plan['development_end_exclusive']) for g in games):
        raise ValueError('Final-test games entered development inputs')
    rows = build_strength_rows(games)
    save_new(output / 'plan.json', plan)
    save_new(output / 'development-features.json', rows)
    specs = candidates(list(rows[0]['features']))
    folds = [partition(rows, *dates) for dates in plan['folds']]
    validation_rows = [dict(r, fold=i) for i, (_, validation) in enumerate(folds) for r in validation]
    if len({r['game_id'] for r in validation_rows}) != len(validation_rows):
        raise ValueError('Overlapping validation folds')
    labels = [r['target'] for r in validation_rows]
    trials, values = [], {}
    for spec in specs:
        predictions, fold_metrics = [], []
        for train, validation in folds:
            p = predict(validation, spec, fit(train, spec))
            predictions.extend(p.tolist())
            fold_metrics.append(scores([r['target'] for r in validation], p))
        values[spec['name']] = np.asarray(predictions)
        trials.append(dict(spec=spec, validation=scores(labels, predictions), folds=fold_metrics))
        print(spec['name'], trials[-1]['validation'], flush=True)
    # A small, declared ensemble search uses only out-of-time development outputs.
    best_by_kind = [min((t for t in trials if t['spec']['kind'] == kind), key=lambda t: t['validation']['log_loss'])
                    for kind in ('normal', 'logistic', 'ridge', 'boost')]
    best_single = min(trials, key=lambda t: t['validation']['log_loss'])
    for candidate in best_by_kind:
        if candidate['spec']['name'] == best_single['spec']['name']:
            continue
        for weight in (.25, .5, .75):
            members = [best_single['spec'], candidate['spec']]
            spec = dict(name=f"blend-{members[0]['name']}-{members[1]['name']}-{weight}",
                        kind='blend', members=members, weights=[1-weight, weight])
            p = (1-weight) * values[members[0]['name']] + weight * values[members[1]['name']]
            values[spec['name']] = p
            offset, fold_metrics = 0, []
            for _, validation in folds:
                fold_metrics.append(scores([r['target'] for r in validation], p[offset:offset+len(validation)]))
                offset += len(validation)
            trials.append(dict(spec=spec, validation=scores(labels, p), folds=fold_metrics))
    if len(trials) > plan['max_candidate_models']:
        raise ValueError('Exceeded declared search budget')
    selected = min(trials, key=lambda t: t['validation']['log_loss'])
    training = [r for r in rows if r['eligible'] and timestamp(r['scheduled_start']) + timedelta(hours=24)
                <= boundary(plan['final_test_start'])]
    model = fit(training, selected['spec'])
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output / 'model.joblib')
    selection = dict(feature_version=VERSION, selected=selected, trials=sorted(trials, key=lambda t: t['validation']['log_loss']),
        created_at=datetime.now(timezone.utc).isoformat(), training_games=len(training),
        last_training_label_available=max(timestamp(r['scheduled_start']) + timedelta(hours=24) for r in training).isoformat(),
        source_hashes=sources, plan_sha256=digest(output / 'plan.json'), model_sha256=digest(output / 'model.joblib'),
        code_hashes={p.name: digest(p) for p in (Path(__file__), Path(__file__).with_name('strength.py'))})
    save_new(output / 'selection.json', selection)
    predictions = [dict(r, model_probability=float(p)) for r, p in zip(validation_rows, values[selected['spec']['name']])]
    save_new(output / 'development-predictions.json', predictions)
    save_new(output / 'development-summary.json', summarize(predictions))
    print('FROZEN', selected, flush=True)


def final_test(output, history_path):
    if (output / 'test-report.json').exists():
        raise ValueError('Final test already scored; preserve the first report')
    selection = json.loads((output / 'selection.json').read_text())
    plan = json.loads((output / 'plan.json').read_text())
    for name, expected in selection['code_hashes'].items():
        if digest(Path(__file__).with_name(name)) != expected:
            raise ValueError('Frozen research implementation changed')
    if digest(output / 'model.joblib') != selection['model_sha256'] or digest(output / 'plan.json') != selection['plan_sha256']:
        raise ValueError('Frozen model or plan changed')
    games, hashes = load_development()
    if hashes != selection['source_hashes']:
        raise ValueError('Development inputs changed after freezing')
    test_data = json.loads(history_path.read_text())
    if not test_data['coverage']['complete']:
        raise ValueError('Final-test history is incomplete')
    first, last = boundary(plan['final_test_start']), boundary(plan['final_test_end_exclusive'])
    expected_days = [(first + timedelta(days=i)).date().isoformat() for i in range((last.date() - first.date()).days)]
    if (test_data['coverage']['start'] != expected_days[0] or test_data['coverage']['end'] != expected_days[-1]
            or [day['date'] for day in test_data['coverage']['days']] != expected_days
            or any(not day['complete'] for day in test_data['coverage']['days'])):
        raise ValueError('Final source does not cover every reserved date')
    if any(not first <= timestamp(g['scheduled_start']) < last for g in test_data['games']):
        raise ValueError('Final source contains games outside the reserved period')
    all_rows = build_strength_rows(games + test_data['games'])
    rows = [r for r in all_rows if first <= timestamp(r['scheduled_start']) < last and r['eligible']]
    excluded = [r['game_id'] for r in all_rows if first <= timestamp(r['scheduled_start']) < last and not r['eligible']]
    # Load only this locally generated, checksum-verified artifact; never untrusted joblib.
    model = joblib.load(output / 'model.joblib')
    p = predict(rows, selection['selected']['spec'], model)
    predictions = [dict(r, model_probability=float(probability)) for r, probability in zip(rows, p)]
    report = dict(**summarize(predictions), predictions=predictions, excluded=excluded,
        expected_games=len(test_data['games']), selection_sha256=digest(output / 'selection.json'),
        history_sha256=digest(history_path), evaluated_at=datetime.now(timezone.utc).isoformat(),
        limitations=['One retrospective season; the search may still overfit development seasons.',
                     '24-hour availability assumption, not verified pregame source publication.',
                     'No injuries, verified lineups or market inputs; no fresh sportsbook comparison.',
                     'Earlier test results may update rating state only after the fixed availability delay.'])
    save_new(output / 'test-report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('predictions', 'calibration')}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('develop', 'test'))
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--history', type=Path)
    args = parser.parse_args()
    with threadpool_limits(limits=1):
        if args.command == 'develop':
            development(args.output)
        elif args.history is None:
            parser.error('--history is required for the final test')
        else:
            final_test(args.output, args.history)


if __name__ == '__main__':
    main()
