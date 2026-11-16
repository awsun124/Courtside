"""Build auditable team baseline features from a collected historical dataset."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.features import FEATURE_NAMES, FEATURE_VERSION, build_features


def build_dataset(source, destination, min_history=5, result_delay_hours=24):
    raw = Path(source).read_bytes()
    dataset = json.loads(raw)
    coverage = dataset["coverage"]
    if coverage.get("complete") is not True:
        raise ValueError("Resolve incomplete source coverage before building features")
    if coverage.get("validated_games") != len(dataset["games"]):
        raise ValueError("Source game count does not match coverage")
    if not dataset["games"]:
        raise ValueError("Source dataset contains no games")
    rows = build_features(dataset["games"], min_history, result_delay_hours)
    report = dict(feature_version=FEATURE_VERSION, source_sha256=hashlib.sha256(raw).hexdigest(),
                  source_coverage=coverage, feature_names=list(FEATURE_NAMES),
                  min_history=min_history, cutoff_minutes_before_tipoff=60,
                  assumed_result_delay_hours=result_delay_hours,
                  games=len(rows), eligible_games=sum(row["eligible"] for row in rows),
                  limitations=["Reconstructed historical features; source publication times are unknown.",
                               "Results become eligible after an assumed delay from scheduled start.",
                               "Rest uses games in this dataset; missing earlier games can bias it.",
                               "History resets each season; no prior-season carryover.",
                               "Raw point margins are not pace- or opponent-adjusted.",
                               "No player, availability, or odds features yet."], rows=rows)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(destination)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-history", type=int, default=5)
    parser.add_argument("--result-delay-hours", type=float, default=24)
    args = parser.parse_args()
    try:
        report = build_dataset(args.source, args.output, args.min_history, args.result_delay_hours)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Feature build failed: {error}", file=sys.stderr)
        return 2
    print(f"Built {report['games']} rows; {report['eligible_games']} have sufficient prior history.")
    print(f"Features: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
