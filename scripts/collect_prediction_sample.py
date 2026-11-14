"""Collect a bounded historical sample; no model training or production writes."""

import argparse
from datetime import date
import json
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.history import PARSER_VERSION, game_ids, parse_game, read_source, timestamp


def collect(day, output, max_games=2, transport=None, sleep=time.sleep):
    day_key = date.fromisoformat(day).strftime("%Y%m%d")
    options = {} if transport is None else {"transport": transport}
    scoreboard, provenance = read_source("scoreboard", day_key, output, **options)
    ids = game_ids(scoreboard, day)
    report = dict(date=day, parser_version=PARSER_VERSION, provider="espn", scope="historical_sample",
                  scoreboard_games=len(ids), attempted_games=min(len(ids), max_games),
                  games=[], players=[], rejected=[], sources=[provenance],
                  limitations=["ESPN IDs require mapping before joining NBA datasets.",
                               "Actual minutes and results are outcomes, not pregame features.",
                               "Original publication times and pregame rosters are unknown.",
                               "Minutes may be rounded; overtime may exceed 240 team minutes.",
                               "No historical odds or injury snapshots were collected."])
    for game_id in ids[:max_games]:
        sleep(1)
        try:
            payload, source = read_source("summary", game_id, output, **options)
            report["sources"].append(source)
            game, players = parse_game(payload, game_id)
            # Compare the actual timestamps, including UTC dates crossing midnight.
            event = next(e for e in scoreboard["events"] if e["id"] == game_id)
            if timestamp(game["scheduled_start"]) != timestamp(event["date"]):
                raise ValueError("Summary start time does not match scoreboard")
            report["games"].append(game)
            report["players"].extend(players)
        except (ValueError, KeyError, TypeError, IndexError) as error:
            report["rejected"].append(dict(game_id="espn:" + game_id, reason=str(error)))
    report["all_listed_games_validated"] = not report["rejected"] and len(report["games"]) == len(ids) and bool(ids)
    report["validated_games"] = len(report["games"])
    report["player_rows"] = len(report["players"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"sample-{day}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2025-10-21", help="Game date in America/New_York")
    parser.add_argument("--max-games", type=int, default=2)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/sample")
    args = parser.parse_args()
    try:
        date.fromisoformat(args.date)
        if not 1 <= args.max_games <= 10:
            parser.error("Use between 1 and 10 games per sample")
        report = collect(args.date, args.output, args.max_games)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Sample collection failed: {error}", file=sys.stderr)
        return 2
    print(f"Validated {report['validated_games']}/{report['scoreboard_games']} listed games; "
          f"{report['player_rows']} player rows; {len(report['rejected'])} rejected games.")
    print(f"Report: {args.output / ('sample-' + args.date + '.json')}")
    return 0 if report["all_listed_games_validated"] else 2


if __name__ == "__main__":
    sys.exit(main())
