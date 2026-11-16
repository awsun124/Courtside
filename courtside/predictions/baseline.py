"""Chronological logistic-regression benchmark for offline research."""

from datetime import datetime, timedelta
import math

from .features import FEATURE_NAMES, FEATURE_VERSION
from .history import EASTERN, timestamp


def split_rows(dataset, settings):
    if dataset["feature_version"] != FEATURE_VERSION or dataset["feature_names"] != list(FEATURE_NAMES):
        raise ValueError("Unsupported feature version or column ordering")
    boundaries = [datetime.fromisoformat(settings[key]).replace(tzinfo=EASTERN)
                  for key in ("train_before", "validation_before", "test_before")]
    if not boundaries[0] < boundaries[1] < boundaries[2]:
        raise ValueError("Split boundaries must increase chronologically")
    partitions = dict(train=[], validation=[], test=[])
    seen, excluded = set(), 0
    for row in sorted(dataset["rows"], key=lambda r: timestamp(r["cutoff_at"])):
        if row["game_id"] in seen:
            raise ValueError("Duplicate feature row")
        seen.add(row["game_id"])
        if type(row["target"]) is not int or row["target"] not in (0, 1):
            raise ValueError("Target must be zero or one")
        if set(row["features"]) != set(FEATURE_NAMES) or any(
                not isinstance(v, (float, int)) or not math.isfinite(v) for v in row["features"].values()):
            raise ValueError("Feature vector is incomplete or nonfinite")
        if not row["eligible"]:
            excluded += 1
            continue
        cutoff = timestamp(row["cutoff_at"])
        # A training/tuning label must be available before the next partition starts.
        label_available = cutoff + timedelta(minutes=dataset["cutoff_minutes_before_tipoff"],
                                              hours=dataset["assumed_result_delay_hours"])
        if cutoff < boundaries[0] and label_available <= boundaries[0]:
            partitions["train"].append(row)
        elif boundaries[0] <= cutoff < boundaries[1] and label_available <= boundaries[1]:
            partitions["validation"].append(row)
        elif boundaries[1] <= cutoff < boundaries[2]:
            partitions["test"].append(row)
        else:
            excluded += 1
    minimum = settings["minimum_partition_games"]
    if type(minimum) is not int or minimum < 2 or any(len(rows) < minimum for rows in partitions.values()):
        raise ValueError("Insufficient games in a chronological partition")
    if len({r["target"] for r in partitions["train"]}) != 2:
        raise ValueError("Training requires examples of both outcomes")
    return partitions, excluded


def scores(labels, probabilities):
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

    return dict(games=len(labels), accuracy=float(accuracy_score(labels, [p >= 0.5 for p in probabilities])),
                brier=float(brier_score_loss(labels, probabilities)),
                log_loss=float(log_loss(labels, probabilities, labels=[0, 1])))


def calibration(labels, probabilities):
    bins = []
    for index in range(10):
        pairs = [(y, p) for y, p in zip(labels, probabilities) if min(int(p * 10), 9) == index]
        if pairs:
            bins.append(dict(lower=index / 10, upper=(index + 1) / 10, games=len(pairs),
                             mean_prediction=sum(p for _, p in pairs) / len(pairs),
                             observed_win_rate=sum(y for y, _ in pairs) / len(pairs)))
    return bins


def fit_baseline(dataset, settings):
    import numpy as np
    import sklearn
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    import warnings

    partitions, excluded = split_rows(dataset, settings)
    def matrix(rows):
        return [[r["features"][key] for key in FEATURE_NAMES] for r in rows]

    train, validation, test = (partitions[key] for key in ("train", "validation", "test"))
    labels = {key: [r["target"] for r in rows] for key, rows in partitions.items()}
    candidates = settings["regularization_candidates"]
    if not candidates or any(not math.isfinite(c) or c <= 0 for c in candidates):
        raise ValueError("Regularization candidates must be positive")
    models, trials = [], []
    for strength in candidates:
        model = make_pipeline(StandardScaler(), LogisticRegression(C=strength, max_iter=2000,
                              random_state=settings["random_seed"]))
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(matrix(train), labels["train"])
        predictions = model.predict_proba(matrix(validation))[:, 1]
        trials.append(dict(C=strength, validation=scores(labels["validation"], predictions)))
        models.append(model)
    selected = min(range(len(trials)), key=lambda i: trials[i]["validation"]["log_loss"])
    model = models[selected]
    home_rate = min(max(sum(labels["train"]) / len(train), 0.01), 0.99)
    report = dict(settings=settings, selected_C=candidates[selected], trials=trials, excluded_rows=excluded,
                  feature_version=FEATURE_VERSION, feature_names=list(FEATURE_NAMES),
                  sklearn_version=sklearn.__version__, numpy_version=np.__version__,
                  baseline_home_probability=home_rate, partitions={}, predictions=[],
                  limitations=dataset.get("limitations", []) + [
                      "Development backtest; not an untouched multi-season evaluation.",
                      "No market odds comparison, player features, or injury inputs.",
                      "Calibration bins describe reliability; no post-hoc calibrator was fitted."])
    for name, rows in partitions.items():
        probabilities = model.predict_proba(matrix(rows))[:, 1]
        report["partitions"][name] = dict(first_cutoff=rows[0]["cutoff_at"], last_cutoff=rows[-1]["cutoff_at"],
            model=scores(labels[name], probabilities),
            home_frequency=scores(labels[name], [home_rate] * len(rows)),
            calibration=calibration(labels[name], probabilities))
        if name != "train":
            report["predictions"].extend(dict(game_id=r["game_id"], cutoff_at=r["cutoff_at"],
                partition=name, target=r["target"], home_probability=float(p)) for r, p in zip(rows, probabilities))
    # Paired date-block bootstrap: negative differences favor the learned model.
    test_predictions = [p for p in report["predictions"] if p["partition"] == "test"]
    by_date = {}
    for row in test_predictions:
        day = timestamp(row["cutoff_at"]).astimezone(EASTERN).date().isoformat()
        difference = (row["home_probability"] - row["target"]) ** 2 - (home_rate - row["target"]) ** 2
        by_date.setdefault(day, []).append(difference)
    blocks = list(by_date.values())
    rng = np.random.default_rng(settings["random_seed"])
    differences = []
    for _ in range(2000):
        sampled = [value for index in rng.integers(0, len(blocks), size=len(blocks)) for value in blocks[index]]
        differences.append(sum(sampled) / len(sampled))
    report["test_brier_difference_95_interval"] = [float(v) for v in np.quantile(differences, [0.025, 0.975])]
    scaler, regression = model.steps[0][1], model.steps[1][1]
    artifact = dict(feature_version=FEATURE_VERSION, feature_names=list(FEATURE_NAMES),
                    mean=scaler.mean_.tolist(), scale=scaler.scale_.tolist(),
                    coefficients=regression.coef_[0].tolist(), intercept=float(regression.intercept_[0]),
                    classes=regression.classes_.tolist(), settings=settings, selected_C=candidates[selected])
    return report, artifact
