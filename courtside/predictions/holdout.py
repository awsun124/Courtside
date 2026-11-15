"""Score existing frozen checkpoints on later games; no fitting or selection."""

from datetime import datetime
import hashlib

from .baseline import calibration, scores
from .elo import predict_elo
from .history import EASTERN, timestamp


def verify_frozen(root, plan):
    for relative, expected in plan["frozen_files"].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen artifact changed: {relative}")


def predict_checkpoint(rows, rotations, elo, metadata, states):
    import numpy as np
    import torch
    from .player_model import PlayerEncoder, tensorize

    if not rows or len(states) != len(metadata["settings"]["seeds"]):
        raise ValueError("Missing holdout rows or seed checkpoints")
    inputs, _, counts = tensorize(rows, rotations, metadata["preprocessing"], elo, metadata["settings"]["max_players"])
    probabilities = []
    for state in states:
        model = PlayerEncoder(len(metadata["preprocessing"]["vocabulary"]), metadata["settings"])
        model.load_state_dict(state, strict=True)
        model.eval()
        with torch.no_grad():
            probabilities.append(torch.sigmoid(model(*inputs)).numpy())
    return np.mean(probabilities, axis=0).tolist(), counts


def paired_interval(rows, first, second, seed):
    import numpy as np

    by_date = {}
    for row in rows:
        day = timestamp(row["cutoff_at"]).astimezone(EASTERN).date().isoformat()
        difference = (row[first] - row["target"]) ** 2 - (row[second] - row["target"]) ** 2
        by_date.setdefault(day, []).append(difference)
    blocks, differences = list(by_date.values()), []
    rng = np.random.default_rng(seed)
    for _ in range(2000):
        sample = [value for index in rng.integers(0, len(blocks), size=len(blocks)) for value in blocks[index]]
        differences.append(sum(sample) / len(sample))
    return [float(v) for v in np.quantile(differences, [0.025, 0.975])]


def evaluate_holdout(history, features, rotations_report, checkpoints, elo_parameters, plan):
    import torch

    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    if (features["assumed_result_delay_hours"] != plan["assumed_result_delay_hours"] or
            features["cutoff_minutes_before_tipoff"] != plan["cutoff_minutes_before_tipoff"] or
            features["min_history"] != plan["minimum_history"] or
            rotations_report["assumed_result_delay_hours"] != plan["assumed_result_delay_hours"] or
            rotations_report["cutoff_minutes_before_tipoff"] != plan["cutoff_minutes_before_tipoff"] or
            rotations_report["min_team_history"] != plan["minimum_history"]):
        raise ValueError("Feature policy differs from frozen holdout plan")
    start, end = (datetime.fromisoformat(plan[key]).replace(tzinfo=EASTERN)
                  for key in ("test_start", "test_end_exclusive"))
    if start >= end:
        raise ValueError("Invalid holdout date range")
    rotations = {r["game_id"]: r for r in rotations_report["rows"]}
    if len(rotations) != len(rotations_report["rows"]):
        raise ValueError("Duplicate holdout rotation")
    game_map = {g["game_id"]: g for g in history["games"]}
    selected, excluded, seen = [], [], set()
    for row in features["rows"]:
        if row["game_id"] in seen:
            raise ValueError("Duplicate holdout feature row")
        seen.add(row["game_id"])
        if not start <= timestamp(row["cutoff_at"]) < end:
            continue
        game = game_map.get(row["game_id"])
        if game is None or int(game["home_win"]) != row["target"]:
            raise ValueError("Holdout feature target does not match source result")
        rotation = rotations.get(row["game_id"])
        if rotation is None:
            raise ValueError("Holdout rotation is missing")
        if row["eligible"] and rotation["eligible"]:
            selected.append(row)
        else:
            excluded.append(row["game_id"])
    selected.sort(key=lambda r: (timestamp(r["cutoff_at"]), r["game_id"]))
    if not selected:
        raise ValueError("No eligible holdout games")
    elo = predict_elo(history["games"], elo_parameters, plan["assumed_result_delay_hours"], plan["cutoff_minutes_before_tipoff"])
    outputs, unknown_counts = {}, {}
    for name, checkpoint in checkpoints.items():
        outputs[name], unknown_counts[name] = predict_checkpoint(selected, rotations, elo,
            checkpoint["metadata"], checkpoint["states"])
    predictions = [dict(game_id=row["game_id"], cutoff_at=row["cutoff_at"], target=row["target"],
        elo_probability=elo[row["game_id"]]["home_probability"],
        **{name + "_probability": values[i] for name, values in outputs.items()}) for i, row in enumerate(selected)]
    columns = {"elo": "elo_probability", **{name: name + "_probability" for name in outputs}}
    def metrics(rows):
        return {name: scores([r["target"] for r in rows], [r[column] for r in rows]) for name, column in columns.items()}
    months = sorted({timestamp(r["cutoff_at"]).astimezone(EASTERN).strftime("%Y-%m") for r in predictions})
    intervals = {name + "_minus_elo": paired_interval(predictions, column, "elo_probability", plan["bootstrap_seed"])
                 for name, column in columns.items() if name != "elo"}
    if "synergy" in outputs and "player" in outputs:
        intervals["synergy_minus_player"] = paired_interval(predictions, "synergy_probability", "player_probability", plan["bootstrap_seed"])
    return dict(plan=plan, elo_parameters=elo_parameters, eligible_games=len(selected), excluded_game_ids=excluded,
        predictions=predictions, aggregate=metrics(predictions),
        months=[dict(month=month, metrics=metrics([r for r in predictions if
            timestamp(r["cutoff_at"]).astimezone(EASTERN).strftime("%Y-%m") == month])) for month in months],
        calibration={name: calibration([r["target"] for r in predictions], [r[column] for r in predictions]) for name, column in columns.items()},
        unknown_player_counts=unknown_counts,
        brier_difference_95_intervals=intervals,
        limitations=["February-April 2025 only; January 2025 had already been inspected.",
                     "Existing 2024 checkpoint weights, vocabulary, scaling, and Elo parameters were loaded unchanged.",
                     "Elo ratings and lagged features update from earlier available results; no weights are refitted.",
                     "Historical publication times and pregame rosters remain approximations.",
                     "No sportsbook comparison or prospective live results."])
