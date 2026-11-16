"""Expanding-history evaluation using the existing baseline fitting procedure."""

from .baseline import calibration, fit_baseline, scores


def evaluate_rolling(dataset, settings):
    folds, models, predictions, seen = [], {}, [], set()
    if not settings["folds"]:
        raise ValueError("At least one rolling fold is required")
    for fold in settings["folds"]:
        if fold["name"] in models:
            raise ValueError("Duplicate rolling fold name")
        options = {key: value for key, value in settings.items() if key != "folds"}
        options.update(fold)
        report, artifact = fit_baseline(dataset, options)
        test = [row for row in report["predictions"] if row["partition"] == "test"]
        for row in test:
            if row["game_id"] in seen:
                raise ValueError("Rolling test games overlap")
            seen.add(row["game_id"])
            predictions.append({**row, "fold": fold["name"],
                                "baseline_probability": report["baseline_home_probability"]})
        folds.append(dict(name=fold["name"], settings=options, selected_C=report["selected_C"],
                          partitions=report["partitions"], validation_trials=report["trials"],
                          test_brier_difference_95_interval=report["test_brier_difference_95_interval"]))
        models[fold["name"]] = artifact
    labels = [row["target"] for row in predictions]
    probabilities = [row["home_probability"] for row in predictions]
    report = dict(settings=settings, folds=folds, predictions=predictions,
                  aggregate=dict(model=scores(labels, probabilities),
                                 home_frequency=scores(labels, [row["baseline_probability"] for row in predictions]),
                                 calibration=calibration(labels, probabilities)),
                  limitations=["Predefined development folds, not a final untouched evaluation.",
                               "Later folds may train on earlier fold outcomes once available.",
                               "Each fold chooses regularization only from its earlier validation period.",
                               "All results retain the feature pipeline's historical timing assumptions.",
                               "No market odds, roster availability, or player synergy inputs."])
    return report, models
