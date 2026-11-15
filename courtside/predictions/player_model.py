"""Offline player encoder with optional masked teammate attention."""

from copy import deepcopy
import math

import numpy as np
import torch
from torch import nn

from .baseline import calibration, scores, split_rows
from .elo import predict_elo
from .features import FEATURE_NAMES
from .players import PLAYER_STATS
from .history import EASTERN, timestamp

RATE_NAMES = tuple(stat + "_per36" for stat in PLAYER_STATS) + ("log_history_games",)


def rate_values(player):
    values = [player["rates"][key] for key in RATE_NAMES[:-1]] + [math.log1p(player["history_games"])]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Nonfinite player input")
    return values


def fit_preprocessing(train, rotations):
    players = [p for row in train for side in ("home", "away") for p in rotations[row["game_id"]]["teams"][side]["players"]]
    vocabulary = {key: index + 1 for index, key in enumerate(sorted({p["player_id"] for p in players}))}
    rates = np.asarray([rate_values(p) for p in players])
    context = np.asarray([[row["features"][key] for key in FEATURE_NAMES] for row in train])
    return dict(vocabulary=vocabulary, rate_names=list(RATE_NAMES), feature_names=list(FEATURE_NAMES),
                rate_mean=rates.mean(axis=0).tolist(), rate_scale=np.where(rates.std(axis=0) > 0, rates.std(axis=0), 1).tolist(),
                context_mean=context.mean(axis=0).tolist(), context_scale=np.where(context.std(axis=0) > 0, context.std(axis=0), 1).tolist())


def tensorize(rows, rotations, preprocessing, elo, max_players):
    ids = np.zeros((len(rows), 2, max_players), dtype=np.int64)
    rates = np.zeros((len(rows), 2, max_players, len(RATE_NAMES)), dtype=np.float32)
    weights = np.zeros((len(rows), 2, max_players), dtype=np.float32)
    unknown = exposures = 0
    for i, row in enumerate(rows):
        rotation = rotations[row["game_id"]]
        if rotation["cutoff_at"] != row["cutoff_at"]:
            raise ValueError("Player and team cutoff timestamps differ")
        for side_index, side in enumerate(("home", "away")):
            players = rotation["teams"][side]["players"]
            if not 5 <= len(players) <= max_players or abs(sum(p["projected_minutes"] for p in players) - 240) > 1e-5:
                raise ValueError("Invalid projected team rotation")
            for j, player in enumerate(players):
                minutes = player["projected_minutes"]
                if not math.isfinite(minutes) or not 0 < minutes <= 48 + 1e-7:
                    raise ValueError("Invalid projected player minutes")
                player_index = preprocessing["vocabulary"].get(player["player_id"], 0)
                ids[i, side_index, j] = player_index
                unknown += int(player_index == 0)
                exposures += 1
                rates[i, side_index, j] = (np.asarray(rate_values(player)) - preprocessing["rate_mean"]) / preprocessing["rate_scale"]
                weights[i, side_index, j] = minutes / 240
    context = (np.asarray([[row["features"][key] for key in FEATURE_NAMES] for row in rows]) -
               preprocessing["context_mean"]) / preprocessing["context_scale"]
    base = [elo[row["game_id"]]["home_probability"] for row in rows]
    base_logits = np.log(np.clip(base, 1e-6, 1 - 1e-6) / (1 - np.clip(base, 1e-6, 1 - 1e-6)))
    tensors = (torch.from_numpy(ids), torch.from_numpy(rates), torch.from_numpy(weights),
               torch.tensor(context, dtype=torch.float32), torch.tensor(base_logits, dtype=torch.float32))
    return tensors, torch.tensor([row["target"] for row in rows], dtype=torch.float32), dict(unknown=unknown, exposures=exposures)


class PlayerEncoder(nn.Module):
    def __init__(self, vocabulary_size, settings):
        super().__init__()
        self.embedding = None
        identity_dimensions = settings["embedding_dim"] if settings.get("use_identity", True) else 0
        if identity_dimensions:
            self.embedding = nn.Embedding(vocabulary_size + 1, identity_dimensions, padding_idx=0)
            nn.init.normal_(self.embedding.weight, std=0.05)
            with torch.no_grad():
                self.embedding.weight[0].zero_()
        self.encoder = nn.Sequential(nn.Linear(identity_dimensions + len(RATE_NAMES), settings["hidden_dim"]),
            nn.Tanh(), nn.Linear(settings["hidden_dim"], settings["player_dim"]), nn.Tanh())
        self.head = nn.Sequential(nn.Linear(settings["player_dim"] * 3 + len(FEATURE_NAMES), settings["hidden_dim"]),
            nn.Tanh(), nn.Dropout(settings["dropout"]), nn.Linear(settings["hidden_dim"], 1))
        nn.init.zeros_(self.head[-1].weight)
        nn.init.zeros_(self.head[-1].bias)
        self.synergy = None
        if settings.get("use_synergy", False):
            self.synergy = SynergyLayer(settings["player_dim"], settings["synergy_heads"],
                                        settings["hidden_dim"], settings["dropout"])

    def forward(self, ids, rates, weights, context, base_logits):
        inputs = torch.cat((self.embedding(ids), rates), dim=-1) if self.embedding is not None else rates
        encoded = self.encoder(inputs)
        if self.synergy is not None:
            batch, sides, players, dimensions = encoded.shape
            encoded = self.synergy(encoded.reshape(batch * sides, players, dimensions),
                                   weights.reshape(batch * sides, players) <= 0).reshape(batch, sides, players, dimensions)
        teams = (encoded * weights.unsqueeze(-1)).sum(dim=2)
        home, away = teams[:, 0], teams[:, 1]
        correction = self.head(torch.cat((home, away, home - away, context), dim=-1)).squeeze(-1)
        return base_logits + correction


class SynergyLayer(nn.Module):
    """One attention block over an unordered set of available roster vectors."""

    def __init__(self, dimensions, heads, hidden, dropout):
        super().__init__()
        self.attention = nn.MultiheadAttention(dimensions, heads, dropout=dropout, batch_first=True)
        self.attention_norm = nn.LayerNorm(dimensions)
        self.feedforward = nn.Sequential(nn.Linear(dimensions, hidden), nn.Tanh(), nn.Linear(hidden, dimensions))
        self.output_norm = nn.LayerNorm(dimensions)

    def forward(self, players, padding):
        if padding.all(dim=1).any():
            raise ValueError("Synergy requires at least one unmasked player per team")
        attended, _ = self.attention(players, players, players, key_padding_mask=padding, need_weights=False)
        contextual = self.attention_norm(players + attended)
        return self.output_norm(contextual + self.feedforward(contextual))


def train_fold(partitions, rotations, elo, settings):
    if (type(settings.get("use_identity", True)) is not bool or type(settings.get("use_synergy", False)) is not bool or
            not settings["seeds"] or len(set(settings["seeds"])) != len(settings["seeds"]) or
            any(type(seed) is not int for seed in settings["seeds"]) or
            any(type(settings[key]) is not int or settings[key] < 1 for key in
                ("embedding_dim", "hidden_dim", "player_dim", "max_players", "max_epochs", "patience", "batch_size")) or
            not 0 <= settings["dropout"] < 1 or settings["learning_rate"] <= 0 or settings["weight_decay"] < 0):
        raise ValueError("Invalid player model settings")
    if settings.get("use_synergy", False) and (type(settings.get("synergy_heads")) is not int or
            settings["synergy_heads"] < 1 or settings["player_dim"] % settings["synergy_heads"]):
        raise ValueError("Synergy heads must divide the player-vector dimension")
    preprocessing = fit_preprocessing(partitions["train"], rotations)
    data = {name: tensorize(rows, rotations, preprocessing, elo, settings["max_players"])
            for name, rows in partitions.items()}
    probabilities = {name: [] for name in partitions}
    checkpoints, states = [], []
    criterion = nn.BCEWithLogitsLoss()
    for seed in settings["seeds"]:
        torch.manual_seed(seed)
        model = PlayerEncoder(len(preprocessing["vocabulary"]), settings)
        optimizer = torch.optim.AdamW(model.parameters(), lr=settings["learning_rate"], weight_decay=settings["weight_decay"])
        model.eval()
        with torch.no_grad():
            best_loss = float(criterion(model(*data["validation"][0]), data["validation"][1]))
        best_state, best_epoch, stale = deepcopy(model.state_dict()), 0, 0
        history = [dict(epoch=0, validation_log_loss=best_loss)]
        train_inputs, train_labels, _ = data["train"]
        for epoch in range(1, settings["max_epochs"] + 1):
            model.train()
            for batch in torch.randperm(len(train_labels)).split(settings["batch_size"]):
                optimizer.zero_grad()
                loss = criterion(model(*(value[batch] for value in train_inputs)), train_labels[batch])
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite neural training loss")
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
            model.eval()
            with torch.no_grad():
                validation_loss = float(criterion(model(*data["validation"][0]), data["validation"][1]))
            history.append(dict(epoch=epoch, validation_log_loss=validation_loss))
            if validation_loss < best_loss - 1e-6:
                best_loss, best_epoch, stale = validation_loss, epoch, 0
                best_state = deepcopy(model.state_dict())
            else:
                stale += 1
            if stale >= settings["patience"]:
                break
        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            for name in partitions:
                probabilities[name].append(torch.sigmoid(model(*data[name][0])).numpy())
        states.append(best_state)
        checkpoints.append(dict(seed=seed, selected_epoch=best_epoch, validation_log_loss=best_loss, history=history))
    return dict(preprocessing=preprocessing, checkpoints=checkpoints,
                unknown_player_counts={name: values[2] for name, values in data.items()},
                parameter_count=sum(p.numel() for p in model.parameters())), states, {
                    name: np.mean(values, axis=0).tolist() for name, values in probabilities.items()}


def evaluate_players(games, features, rotations_report, rolling_settings, elo_report, settings):
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    rotations = {row["game_id"]: row for row in rotations_report["rows"]}
    if len(rotations) != len(rotations_report["rows"]) or set(rotations) != {r["game_id"] for r in features["rows"]}:
        raise ValueError("Full player rotation and team feature inventories must match")
    filtered = deepcopy(features)
    for row in filtered["rows"]:
        row["eligible"] = row["eligible"] and rotations[row["game_id"]]["eligible"]
    folds, models, predictions, seen = [], {}, [], set()
    elo_folds = {fold["name"]: fold for fold in elo_report["folds"]}
    for fold in rolling_settings["folds"]:
        options = {key: value for key, value in rolling_settings.items() if key != "folds"}
        options.update(fold)
        partitions, _ = split_rows(filtered, options)
        elo = predict_elo(games, elo_folds[fold["name"]]["selected_parameters"],
                          features["assumed_result_delay_hours"], features["cutoff_minutes_before_tipoff"])
        reference = {r["game_id"]: r for r in elo_report["predictions"] if r["fold"] == fold["name"]}
        for row in partitions["test"]:
            saved = reference.get(row["game_id"])
            if (saved is None or saved["target"] != row["target"] or
                    abs(saved["home_probability"] - elo[row["game_id"]]["home_probability"]) > 1e-10):
                raise ValueError("Player test games do not match the saved Elo benchmark")
        training, states, probabilities = train_fold(partitions, rotations, elo, settings)
        metrics = {}
        for name, rows in partitions.items():
            labels = [row["target"] for row in rows]
            metrics[name] = dict(player_model=scores(labels, probabilities[name]),
                                 elo=scores(labels, [elo[row["game_id"]]["home_probability"] for row in rows]))
            if name == "test":
                for row, probability in zip(rows, probabilities[name]):
                    if row["game_id"] in seen:
                        raise ValueError("Overlapping player test games")
                    seen.add(row["game_id"])
                    predictions.append(dict(game_id=row["game_id"], cutoff_at=row["cutoff_at"], fold=fold["name"],
                        target=row["target"], home_probability=probability,
                        elo_probability=elo[row["game_id"]]["home_probability"]))
        folds.append(dict(name=fold["name"], partitions=metrics, **training))
        models[fold["name"]] = dict(states=states, preprocessing=training["preprocessing"], settings=settings)
    labels = [r["target"] for r in predictions]
    by_date = {}
    for row in predictions:
        day = timestamp(row["cutoff_at"]).astimezone(EASTERN).date().isoformat()
        difference = (row["home_probability"] - row["target"]) ** 2 - (row["elo_probability"] - row["target"]) ** 2
        by_date.setdefault(day, []).append(difference)
    blocks = list(by_date.values())
    rng = np.random.default_rng(rolling_settings["random_seed"])
    differences = []
    for _ in range(2000):
        sample = [value for index in rng.integers(0, len(blocks), size=len(blocks)) for value in blocks[index]]
        differences.append(sum(sample) / len(sample))
    return dict(settings=settings, folds=folds, predictions=predictions,
                player_minus_elo_brier_95_interval=[float(v) for v in np.quantile(differences, [0.025, 0.975])],
                aggregate=dict(player_model=scores(labels, [r["home_probability"] for r in predictions]),
                               elo=scores(labels, [r["elo_probability"] for r in predictions])),
                calibration=calibration(labels, [r["home_probability"] for r in predictions]),
                limitations=["Development model with approximate rotations and no pregame injury or transaction feed.",
                             ("One teammate attention block; not observed five-player lineup interactions."
                              if settings.get("use_synergy", False) else "No explicit synergy or attention layer."),
                             "Elo parameters are inherited from the existing validation-selected benchmark.",
                             "Epoch zero is unchanged Elo; validation may reject learned corrections.",
                             "No sportsbook comparison or final untouched test."]), models
