"""Merge complete historical datasets and audit full NBA season counts."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.features import validate_games
from courtside.predictions.history import timestamp


def merge_results(paths, output, full_seasons=False):
    games, sources = [], []
    for path in paths:
        raw = Path(path).read_bytes()
        dataset = json.loads(raw)
        if dataset["coverage"].get("complete") is not True:
            raise ValueError(f"Incomplete source coverage: {path}")
        if len(dataset["games"]) != dataset["coverage"]["validated_games"]:
            raise ValueError("Source game count does not match coverage")
        games.extend(dataset["games"])
        sources.append(dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), coverage=dataset["coverage"]))
    if not games:
        raise ValueError("No games to merge")
    validate_games(games)
    counts, teams = Counter(), defaultdict(Counter)
    for game in games:
        counts[game["season"]] += 1
        for side in ("home", "away"):
            teams[game["season"]][game[side]["team_id"]] += 1
    audits = {}
    for season, count in sorted(counts.items()):
        complete = count == 1230 and len(teams[season]) == 30 and set(teams[season].values()) == {82}
        audits[season] = dict(games=count, teams=len(teams[season]), team_game_counts=dict(teams[season]),
                              standard_82_game_season_counts_match=complete)
        if full_seasons and not complete:
            raise ValueError(f"Full-season audit failed for {season}: {count} games; check exclusions and missing dates")
    games.sort(key=lambda game: (timestamp(game["scheduled_start"]), game["game_id"]))
    coverage = dict(complete=True, validated_games=len(games), player_rows=0, sources=sources,
                    season_audits=audits, full_season_counts_required=full_seasons,
                    completeness_basis="Source inventory plus per-season game counts when requested; no independent schedule cross-check")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(coverage=coverage, games=games, players=[]), indent=2) + "\n")
    temporary.replace(destination)
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-seasons", action="store_true", help="Require 1,230 games and 82 games per team per season")
    args = parser.parse_args()
    try:
        coverage = merge_results(args.sources, args.output, args.full_seasons)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Merge failed: {error}", file=sys.stderr)
        return 2
    print(f"Merged {coverage['validated_games']} games across {len(coverage['season_audits'])} seasons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
