"""Collect daily final scores for the team baseline without player box scores."""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.history import game_ids, parse_result, read_source, timestamp, PARSER_VERSION


def collect_results(start, end, output, transport=None, sleep=time.sleep):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last - first).days < 366:
        raise ValueError("Choose an ordered range of at most 366 days")
    output = Path(output)
    games, days, sources, seen = [], [], [], set()
    options = {} if transport is None else {"transport": transport}
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        print(f"Collecting results {day}", flush=True)
        sleep(1)
        status = dict(date=day.isoformat(), listed_games=None, validated_games=0, rejected=[], excluded=[])
        try:
            payload, source = read_source("scoreboard", day.strftime("%Y%m%d"), output, **options)
            sources.append(source)
            # ESPN may list overseas preseason games on the adjacent Eastern date.
            # Validate day scope for the regular-season inventory this collector uses.
            regular = dict(payload, events=[event for event in payload["events"] if event["season"]["type"] == 2])
            game_ids(regular, day.isoformat())
            status["listed_games"] = len(payload["events"])
            for event in payload["events"]:
                try:
                    if event["season"]["type"] != 2:
                        status["excluded"].append(dict(game_id=event["id"], reason="Not regular season"))
                        continue
                    status_name = event["competitions"][0]["status"]["type"].get("name")
                    if status_name in {"STATUS_POSTPONED", "STATUS_CANCELED"}:
                        status["excluded"].append(dict(game_id=event["id"], reason=status_name))
                        continue
                    # The NBA Cup final is an exhibition for season-record purposes.
                    notes = " ".join(n.get("headline", "") for c in event["competitions"] for n in c.get("notes", []))
                    if any(c.get("type", {}).get("abbreviation") == "ALLSTAR" for c in event["competitions"]):
                        status["excluded"].append(dict(game_id=event["id"], reason="All-Star exhibition"))
                        continue
                    if "championship" in notes.lower() and any(
                            name in notes.lower() for name in ("cup", "in-season tournament")):
                        status["excluded"].append(dict(game_id=event["id"], reason="NBA Cup final"))
                        continue
                    game = parse_result(dict(header=event), event["id"])
                    if timestamp(game["scheduled_start"]) != timestamp(event["date"]):
                        raise ValueError("Event and competition dates differ")
                    if game["game_id"] in seen:
                        raise ValueError("Duplicate game across dates")
                    seen.add(game["game_id"])
                    games.append(game)
                    status["validated_games"] += 1
                except (ValueError, KeyError, TypeError, IndexError) as error:
                    status["rejected"].append(dict(game_id=event.get("id"), reason=str(error)))
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            status["rejected"].append(dict(reason=str(error)))
        status["complete"] = not status["rejected"] and status["listed_games"] is not None
        days.append(status)
    games.sort(key=lambda g: (timestamp(g["scheduled_start"]), g["game_id"]))
    coverage = dict(start=start, end=end, complete=all(d["complete"] for d in days),
                    provider="espn", parser_version=PARSER_VERSION, validated_games=len(games),
                    player_rows=0, scope="Daily scoreboard final results only", days=days,
                    completeness_basis="Daily source inventory, not an independent schedule", sources=sources)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"results-{start}-{end}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(coverage=coverage, games=games, players=[]), indent=2) + "\n")
    temporary.replace(path)
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/results")
    args = parser.parse_args()
    try:
        coverage = collect_results(args.start, args.end, args.output)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"Validated {coverage['validated_games']} results; complete: {coverage['complete']}")
    return 0 if coverage["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
