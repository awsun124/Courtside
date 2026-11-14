"""Pregame win/loss Elo with delayed updates and season regression."""

from datetime import timedelta
import heapq
import math

from .baseline import calibration, scores, split_rows
from .features import validate_games
from .history import EASTERN, timestamp


def predict_elo(games, parameters, result_delay_hours=24, cutoff_minutes=60):
    validate_games(games)
    initial = parameters["initial_rating"]
    scale = parameters["rating_scale"]
    retention = parameters["season_retention"]
    k = parameters["k"]
    home_advantage = parameters["home_advantage"]
    if (not all(math.isfinite(v) for v in (initial, scale, retention, k, home_advantage,
                                          result_delay_hours, cutoff_minutes)) or
            scale <= 0 or k <= 0 or not 0 <= retention <= 1 or home_advantage < 0 or
            result_delay_hours < 6 or cutoff_minutes < 0):
        raise ValueError("Invalid Elo settings")
    ratings, pending, predictions = {}, [], {}
    season = None
    for game in sorted(games, key=lambda g: (timestamp(g["scheduled_start"]), g["game_id"])):
        start = timestamp(game["scheduled_start"])
        cutoff = start - timedelta(minutes=cutoff_minutes)
        while pending and pending[0][0] <= cutoff:
            _, _, previous, expected = heapq.heappop(pending)
            change = k * (int(previous["home_win"]) - expected)
            ratings[previous["home"]["team_id"]] += change
            ratings[previous["away"]["team_id"]] -= change
        if season is not None and game["season"] != season:
            ratings = {team: initial + retention * (rating - initial) for team, rating in ratings.items()}
        season = game["season"]
        home, away = game["home"]["team_id"], game["away"]["team_id"]
        ratings.setdefault(home, initial)
        ratings.setdefault(away, initial)
        advantage = home_advantage if game.get("neutral_site") is False else 0
        probability = 1 / (1 + 10 ** ((ratings[away] - ratings[home] - advantage) / scale))
        predictions[game["game_id"]] = dict(game_id=game["game_id"], cutoff_at=cutoff.isoformat(),
            home_probability=probability, home_rating=ratings[home], away_rating=ratings[away])
        # The outcome is applied only after its assumed availability time.
        heapq.heappush(pending, (start + timedelta(hours=result_delay_hours), game["game_id"], game, probability))
    return predictions


def compare_elo(games, features, rolling, settings):
    partitions, predictions, seen = [], [], set()
    game_map = {g["game_id"]: g for g in games}
    validate_games(games)
    for row in features["rows"]:
        game = game_map.get(row["game_id"])
        if (game is None or int(game["home_win"]) != row["target"] or
                timestamp(game["scheduled_start"]) - timedelta(minutes=features["cutoff_minutes_before_tipoff"]) != timestamp(row["cutoff_at"])):
            raise ValueError("Elo results and feature rows do not match")
    if set(game_map) != {row["game_id"] for row in features["rows"]}:
        raise ValueError("Elo and logistic regression must use the same source games")
    candidates = []
    for k in settings["k_candidates"]:
        for advantage in settings["home_advantage_candidates"]:
            parameters = {key: settings[key] for key in ("initial_rating", "rating_scale", "season_retention")}
            parameters.update(k=k, home_advantage=advantage)
            candidates.append((parameters, predict_elo(games, parameters,
                features["assumed_result_delay_hours"], features["cutoff_minutes_before_tipoff"])))
    if not candidates:
        raise ValueError("Elo requires parameter candidates")
    for fold in rolling["folds"]:
        options = {key: value for key, value in rolling["settings"].items() if key != "folds"}
        options.update(fold["settings"])
        split, _ = split_rows(features, options)
        validation, test = split["validation"], split["test"]
        trials = [dict(parameters=parameters, validation=scores([r["target"] for r in validation],
                    [values[r["game_id"]]["home_probability"] for r in validation]))
                  for parameters, values in candidates]
        selected = min(range(len(trials)), key=lambda i: trials[i]["validation"]["log_loss"])
        values = candidates[selected][1]
        logistic = {row["game_id"]: row for row in rolling["predictions"] if row["fold"] == fold["name"]}
        if set(logistic) != {row["game_id"] for row in test}:
            raise ValueError("Elo and logistic test games differ")
        for row in test:
            key = row["game_id"]
            if key in seen or logistic[key]["target"] != row["target"]:
                raise ValueError("Overlapping games or mismatched test targets")
            seen.add(key)
            predictions.append({**values[key], "fold": fold["name"], "target": row["target"],
                "logistic_probability": logistic[key]["home_probability"],
                "baseline_probability": logistic[key]["baseline_probability"]})
        partitions.append(dict(name=fold["name"], selected_parameters=trials[selected]["parameters"],
            validation_trials=trials, elo=scores([r["target"] for r in test], [values[r["game_id"]]["home_probability"] for r in test]),
            logistic=fold["partitions"]["test"]["model"], home_frequency=fold["partitions"]["test"]["home_frequency"]))
    labels = [r["target"] for r in predictions]
    import numpy as np
    by_date = {}
    for row in predictions:
        day = timestamp(row["cutoff_at"]).astimezone(EASTERN).date().isoformat()
        difference = (row["home_probability"] - row["target"]) ** 2 - (row["logistic_probability"] - row["target"]) ** 2
        by_date.setdefault(day, []).append(difference)
    blocks = list(by_date.values())
    rng = np.random.default_rng(rolling["settings"]["random_seed"])
    differences = []
    for _ in range(2000):
        sample = [value for index in rng.integers(0, len(blocks), size=len(blocks)) for value in blocks[index]]
        differences.append(sum(sample) / len(sample))
    return dict(settings=settings, folds=partitions, predictions=predictions,
                aggregate={name: scores(labels, [r[key] for r in predictions]) for name, key in (
                    ("elo", "home_probability"), ("logistic", "logistic_probability"), ("home_frequency", "baseline_probability"))},
                calibration=calibration(labels, [r["home_probability"] for r in predictions]),
                elo_minus_logistic_brier_95_interval=[float(v) for v in np.quantile(differences, [0.025, 0.975])],
                limitations=["Simple win/loss Elo; no margin-of-victory multiplier or roster adjustments.",
                             "Ratings update during validation/test only after assumed result availability.",
                             "Initial ratings are equal; no pre-2022-23 history is available.",
                             "This is a development benchmark, not a sportsbook comparison."])
