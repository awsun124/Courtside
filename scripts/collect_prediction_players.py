"""Collect a resumable prefix of player box scores for each historical season."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.features import validate_games
from courtside.predictions.history import fetch_bytes, read_source, timestamp, PARSER_VERSION
from courtside.predictions.players import parse_player_game, PLAYER_FEATURE_VERSION


def collect_players(source, output, games_per_season=120, transport=None, sleep=time.sleep):
    raw = Path(source).read_bytes()
    history = json.loads(raw)
    if history["coverage"].get("complete") is not True:
        raise ValueError("Source game history must be complete")
    if type(games_per_season) is not int or not 1 <= games_per_season <= 1230:
        raise ValueError("Choose 1 to 1230 games per season")
    validate_games(history["games"])
    seasons = defaultdict(list)
    for game in sorted(history["games"], key=lambda g: (timestamp(g["scheduled_start"]), g["game_id"])):
        seasons[game["season"]].append(game)
    selected = [g for season in sorted(seasons) for g in seasons[season][:games_per_season]]
    def fetch(url):
        sleep(1)
        return (transport or fetch_bytes)(url)
    games, players, observations, sources, rejected = [], [], [], [], []
    for index, game in enumerate(selected):
        if index % 10 == 0:
            print(f"Player box scores {index}/{len(selected)}", flush=True)
        try:
            if not game["game_id"].startswith("espn:"):
                raise ValueError("Expected namespaced ESPN game ID")
            payload, provenance = read_source("summary", game["game_id"][5:], output, transport=fetch)
            logs, observed = parse_player_game(payload, game)
            games.append(game)
            players.extend(logs)
            observations.extend(observed)
            sources.append(provenance)
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            rejected.append(dict(game_id=game["game_id"], reason=str(error)))
    coverage = dict(source_sha256=hashlib.sha256(raw).hexdigest(), source_games=len(history["games"]),
                    parser_version=PARSER_VERSION, player_feature_version=PLAYER_FEATURE_VERSION,
                    selected_games=len(selected), validated_games=len(games), player_rows=len(players),
                    unknown_participation_rows=sum(row["participation_unknown"] for row in observations),
                    selected_complete=not rejected and bool(selected),
                    full_history_complete=not rejected and len(games) == len(history["games"]),
                    selection="Chronological prefix per season", games_per_season=games_per_season,
                    seasons={season: dict(source_games=len(values), selected_games=min(len(values), games_per_season))
                             for season, values in sorted(seasons.items())}, rejected=rejected)
    dataset = dict(coverage=coverage, games=games, players=players, observations=observations, sources=sources)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"players-prefix-{games_per_season}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(dataset, indent=2) + "\n")
    temporary.replace(destination)
    return dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--games-per-season", type=int, default=120)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/players")
    args = parser.parse_args()
    try:
        dataset = collect_players(args.source, args.output, args.games_per_season)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Player collection failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(dataset["coverage"], indent=2))
    return 0 if dataset["coverage"]["selected_complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
