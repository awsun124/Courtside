"""Pregame team features from historical results, with explicit availability assumptions."""

from datetime import timedelta
import math

from .history import EASTERN, timestamp

FEATURE_VERSION = 1
FEATURE_NAMES = (
    "home_court", "neutral_site_unknown", "home_history_games", "away_history_games",
    "win_rate_difference", "margin_difference", "recent_margin_difference",
    "home_rest_days", "away_rest_days", "home_rest_unknown", "away_rest_unknown",
    "home_back_to_back", "away_back_to_back",
)


def validate_games(games):
    seen = set()
    for game in games:
        if not isinstance(game["game_id"], str) or not game["game_id"] or game["game_id"] in seen:
            raise ValueError("Missing or duplicate game ID")
        seen.add(game["game_id"])
        timestamp(game["scheduled_start"])
        if not isinstance(game["season"], str) or not game["season"]:
            raise ValueError("Missing season")
        if game["home"]["team_id"] == game["away"]["team_id"]:
            raise ValueError("Game teams must be distinct")
        for side in ("home", "away"):
            team = game[side]
            if not isinstance(team["team_id"], str) or not team["team_id"]:
                raise ValueError("Missing team ID")
            if type(team["points"]) is not int or team["points"] < 0:
                raise ValueError("Invalid final score")
        if (type(game["home_win"]) is not bool or game["home"]["points"] == game["away"]["points"] or
                game["home_win"] != (game["home"]["points"] > game["away"]["points"])):
            raise ValueError("Result does not match final scores")
        if game.get("neutral_site") is not None and type(game["neutral_site"]) is not bool:
            raise ValueError("Invalid neutral-site indicator")


def team_form(history, team_id, window):
    recent = history[-window:]
    margins = []
    for game in recent:
        side = "home" if game["home"]["team_id"] == team_id else "away"
        opponent = "away" if side == "home" else "home"
        margins.append(game[side]["points"] - game[opponent]["points"])
    if not margins:
        return 0.5, 0.0
    return sum(margin > 0 for margin in margins) / len(margins), sum(margins) / len(margins)


def rest_features(schedule, tipoff):
    if not schedule:
        return dict(rest_days=0, rest_unknown=1, back_to_back=0)
    # Calendar dates capture back-to-backs even across DST and differing tipoff times.
    days = (tipoff.astimezone(EASTERN).date() -
            timestamp(schedule[-1]["scheduled_start"]).astimezone(EASTERN).date()).days
    return dict(rest_days=min(max(days - 1, 0), 7), rest_unknown=0, back_to_back=int(days == 1))


def build_features(games, min_history=5, result_delay_hours=24):
    """Return features and labels separately; no current-game result enters inputs.

    Historical publication times are unknown. Treat results as available only
    after scheduled start + result_delay_hours, not as verified source timestamps.
    """
    if type(min_history) is not int or min_history < 1:
        raise ValueError("Minimum history must be a positive integer")
    if not math.isfinite(result_delay_hours) or result_delay_hours < 6:
        raise ValueError("Use a conservative result availability delay of at least six hours")
    validate_games(games)
    ordered = sorted(games, key=lambda game: (timestamp(game["scheduled_start"]), game["game_id"]))
    rows = []
    for index, game in enumerate(ordered):
        tipoff = timestamp(game["scheduled_start"])
        cutoff = tipoff - timedelta(hours=1)
        previous = [g for g in ordered[:index] if g["season"] == game["season"] and
                    timestamp(g["scheduled_start"]) < cutoff]
        forms, rests, ids = {}, {}, {}
        for side in ("home", "away"):
            team_id = game[side]["team_id"]
            schedule = [g for g in previous if team_id in (g["home"]["team_id"], g["away"]["team_id"])]
            history = [g for g in schedule if timestamp(g["scheduled_start"]) +
                       timedelta(hours=result_delay_hours) <= cutoff]
            win_rate, margin = team_form(history, team_id, 10)
            _, recent_margin = team_form(history, team_id, 5)
            forms[side] = dict(count=len(history), win_rate=win_rate, margin=margin, recent_margin=recent_margin)
            rests[side] = rest_features(schedule, tipoff)
            ids[side] = [g["game_id"] for g in history]
        home, away = forms["home"], forms["away"]
        features = dict(home_court=int(game.get("neutral_site") is False),
                        neutral_site_unknown=int(game.get("neutral_site") is None),
                        home_history_games=home["count"], away_history_games=away["count"],
                        win_rate_difference=home["win_rate"] - away["win_rate"],
                        margin_difference=home["margin"] - away["margin"],
                        recent_margin_difference=home["recent_margin"] - away["recent_margin"])
        for side in ("home", "away"):
            features.update({side + "_" + key: value for key, value in rests[side].items()})
        rows.append(dict(game_id=game["game_id"], season=game["season"], cutoff_at=cutoff.isoformat(),
                         features={name: features[name] for name in FEATURE_NAMES},
                         target=int(game["home_win"]), history_game_ids=ids,
                         rest_source_game_ids={side: next((g["game_id"] for g in reversed(previous)
                             if game[side]["team_id"] in (g["home"]["team_id"], g["away"]["team_id"])), None)
                             for side in ("home", "away")},
                         eligible=home["count"] >= min_history and away["count"] >= min_history))
    return rows
