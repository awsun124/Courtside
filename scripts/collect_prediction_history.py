"""Build a cached historical dataset over an inclusive range of Eastern dates."""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.collect_prediction_sample import collect
from courtside.predictions.history import PARSER_VERSION


def collect_range(start, end, output, transport=None, sleep=time.sleep):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last - first).days < 366:
        raise ValueError("Choose an ordered date range of at most 366 days")
    output = Path(output)
    games, players, days, seen = [], [], [], set()
    for offset in range((last - first).days + 1):
        day = (first + timedelta(days=offset)).isoformat()
        print(f"Collecting {day}", flush=True)
        sleep(1)
        try:
            report = collect(day, output, max_games=15, transport=transport, sleep=sleep)
            day_errors = list(report["rejected"])
            for game in report["games"]:
                game_id = game["game_id"]
                if game_id in seen:
                    day_errors.append(dict(game_id=game_id, reason="Game appears on multiple dates"))
                    continue
                seen.add(game_id)
                games.append(game)
                players.extend(p for p in report["players"] if p["game_id"] == game_id)
            # An empty day is valid for a date range, unlike a requested sample.
            complete = not day_errors and report["validated_games"] == report["scoreboard_games"]
            days.append(dict(date=day, listed_games=report["scoreboard_games"],
                             validated_games=report["validated_games"], complete=complete,
                             rejected=day_errors))
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            days.append(dict(date=day, listed_games=None, validated_games=0,
                             complete=False, rejected=[dict(reason=str(error))]))
    games.sort(key=lambda game: (game["scheduled_start"], game["game_id"]))
    players.sort(key=lambda player: (player["game_id"], player["team_id"], player["player_id"]))
    coverage = dict(start=start, end=end, provider="espn", parser_version=PARSER_VERSION,
                    complete=all(day["complete"] for day in days),
                    validated_games=len(games), player_rows=len(players), days=days,
                    completeness_basis="All events returned by each daily scoreboard, not an independent season schedule",
                    pregame_availability="unknown", odds_coverage="not collected",
                    id_namespace="espn", publication_times="unknown",
                    scope="Source season type 2; Cup final/exhibition exclusions need a separate schedule audit")
    output.mkdir(parents=True, exist_ok=True)
    # Publish one dataset file so its games and coverage always describe one run.
    destination = output / f"history-{start}-{end}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(coverage=coverage, games=games, players=players), indent=2) + "\n")
    temporary.replace(destination)
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/history")
    args = parser.parse_args()
    try:
        coverage = collect_range(args.start, args.end, args.output)
    except (ValueError, OSError) as error:
        print(f"Collection failed: {error}", file=sys.stderr)
        return 2
    print(f"Validated {coverage['validated_games']} games / {coverage['player_rows']} player rows. "
          f"All listed events validated: {coverage['complete']}")
    print(f"Dataset: {args.output / ('history-' + args.start + '-' + args.end + '.json')}")
    return 0 if coverage["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
