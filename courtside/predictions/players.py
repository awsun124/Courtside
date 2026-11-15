"""Historical player records and conservative, lagged rotation estimates."""

from collections import defaultdict
from datetime import timedelta
import math

from .history import identifier, parse_game, timestamp

PLAYER_FEATURE_VERSION = 1
PLAYER_STATS = ("points", "rebounds", "assists", "turnovers", "steals", "blocks",
                "field_goals_made", "field_goals_attempted", "three_pointers_made",
                "three_pointers_attempted", "free_throws_made", "free_throws_attempted")


def parse_player_game(payload, expected):
    game, logs = parse_game(payload, expected["game_id"].removeprefix("espn:"))
    if (game["season"] != expected["season"] or timestamp(game["scheduled_start"]) != timestamp(expected["scheduled_start"]) or
            any(game[side][key] != expected[side][key] for side in ("home", "away") for key in ("team_id", "points"))):
        raise ValueError("Player summary does not match the historical game")
    observations = []
    for group in payload["boxscore"]["players"]:
        team_id = identifier(group["team"]["id"])
        table = group["statistics"][0]
        for athlete in table["athletes"]:
            stats = dict(zip(table["labels"], athlete["stats"]))
            observations.append(dict(game_id=game["game_id"], player_id=identifier(athlete["athlete"]["id"]),
                team_id=team_id, did_not_play=athlete.get("didNotPlay") is True,
                participation_unknown=athlete.get("didNotPlay") is not True and stats.get("MIN") == "--"))
    return logs, observations


def allocate_minutes(weights):
    """Scale positive historical weights to 240 minutes, capping a player at 48."""
    if any(not math.isfinite(value) or value < 0 for value in weights.values()):
        raise ValueError("Minute weights must be finite and nonnegative")
    active = {key: value for key, value in weights.items() if value > 0}
    if len(active) < 5:
        return {}
    assigned, remaining = {}, 240.0
    while active:
        total = sum(active.values())
        proposed = {key: remaining * value / total for key, value in active.items()}
        capped = [key for key, value in proposed.items() if value > 48]
        if not capped:
            assigned.update(proposed)
            break
        for key in capped:
            assigned[key] = 48.0
            remaining -= 48
            del active[key]
    return assigned


def build_rotations(dataset, min_team_history=5, result_delay_hours=24):
    """Use only earlier box-score appearances; this is not a true pregame roster."""
    from .features import validate_games
    games = dataset["games"]
    validate_games(games)
    if dataset["coverage"].get("selected_complete") is not True:
        raise ValueError("Resolve rejected or missing selected box scores first")
    if type(min_team_history) is not int or min_team_history < 1 or not math.isfinite(result_delay_hours) or result_delay_hours < 6:
        raise ValueError("Invalid rotation history or result delay")
    game_map = {g["game_id"]: g for g in games}
    logs_by_game, observations_by_game = defaultdict(list), defaultdict(list)
    seen = set()
    for row in dataset["players"]:
        key = (row["game_id"], row["player_id"])
        if key in seen or row["game_id"] not in game_map:
            raise ValueError("Duplicate player log or unknown game")
        seen.add(key)
        if any(not isinstance(row[key], (int, float)) or not math.isfinite(row[key]) or row[key] < 0
               for key in ("minutes", *PLAYER_STATS)):
            raise ValueError("Invalid player statistic")
        if row["team_id"] not in {game_map[row["game_id"]][side]["team_id"] for side in ("home", "away")}:
            raise ValueError("Player log belongs to another team")
        logs_by_game[row["game_id"]].append(row)
    seen_observations = set()
    for row in dataset["observations"]:
        key = (row["game_id"], row["player_id"])
        if key in seen_observations or row["game_id"] not in game_map:
            raise ValueError("Duplicate roster observation or unknown game")
        seen_observations.add(key)
        if row["team_id"] not in {game_map[row["game_id"]][side]["team_id"] for side in ("home", "away")}:
            raise ValueError("Observation belongs to another team")
        if type(row["did_not_play"]) is not bool:
            raise ValueError("Observation is missing a DNP indicator")
        observations_by_game[row["game_id"]].append(row)
    played = {(row["game_id"], row["player_id"], row["team_id"]) for rows in logs_by_game.values() for row in rows}
    observed_played = {(row["game_id"], row["player_id"], row["team_id"])
                      for rows in observations_by_game.values() for row in rows
                      if not row["did_not_play"] and not row.get("participation_unknown", False)}
    if played != observed_played:
        raise ValueError("Played-player observations do not match player logs")
    output = []
    ordered = sorted(games, key=lambda g: (timestamp(g["scheduled_start"]), g["game_id"]))
    for game in ordered:
        cutoff = timestamp(game["scheduled_start"]) - timedelta(hours=1)
        past = [g for g in ordered if g["season"] == game["season"] and
                timestamp(g["scheduled_start"]) + timedelta(hours=result_delay_hours) <= cutoff]
        latest, player_history = {}, defaultdict(list)
        for previous in past:
            for row in observations_by_game[previous["game_id"]]:
                latest[row["player_id"]] = (row["team_id"], timestamp(previous["scheduled_start"]))
            for row in logs_by_game[previous["game_id"]]:
                player_history[row["player_id"]].append(row)
        teams = {}
        for side in ("home", "away"):
            team_id = game[side]["team_id"]
            team_games = [g for g in past if team_id in (g["home"]["team_id"], g["away"]["team_id"])]
            recent = team_games[-5:]
            candidates = [player for player, (team, observed) in latest.items() if team == team_id and
                          cutoff - observed <= timedelta(days=30)]
            weights = dict.fromkeys(candidates, 0.0)
            for previous in recent:
                team_logs = [row for row in logs_by_game[previous["game_id"]] if row["team_id"] == team_id]
                total = sum(row["minutes"] for row in team_logs)
                if total <= 0:
                    raise ValueError("Missing team minutes in historical box score")
                for row in team_logs:
                    if row["player_id"] in weights:
                        # Normalize overtime and rounded totals before averaging past games.
                        weights[row["player_id"]] += 240 * row["minutes"] / total / len(recent)
            projected = allocate_minutes(weights)
            vectors = []
            for player_id, minutes in sorted(projected.items()):
                history = player_history[player_id][-10:]
                total_minutes = sum(row["minutes"] for row in history)
                rates = {stat + "_per36": 36 * sum(row[stat] for row in history) / total_minutes
                         if total_minutes else 0.0 for stat in PLAYER_STATS}
                vectors.append(dict(player_id=player_id, team_id=team_id, projected_minutes=minutes,
                                    history_games=len(history), rates=rates,
                                    history_game_ids=[row["game_id"] for row in history]))
            teams[side] = dict(team_id=team_id, history_games=len(team_games),
                               minute_source_game_ids=[g["game_id"] for g in recent], players=vectors,
                               eligible=len(team_games) >= min_team_history and bool(projected))
        output.append(dict(game_id=game["game_id"], cutoff_at=cutoff.isoformat(), teams=teams,
                           eligible=all(team["eligible"] for team in teams.values())))
    return output
