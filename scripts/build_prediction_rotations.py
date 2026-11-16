"""Build historical player-rate vectors and estimated rotations without future box scores."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.players import build_rotations, PLAYER_FEATURE_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = args.source.read_bytes()
        dataset = json.loads(raw)
        rows = build_rotations(dataset)
        report = dict(player_feature_version=PLAYER_FEATURE_VERSION,
                      source_sha256=hashlib.sha256(raw).hexdigest(), source_coverage=dataset["coverage"],
                      games=len(rows), eligible_games=sum(r["eligible"] for r in rows),
                      cutoff_minutes_before_tipoff=60, assumed_result_delay_hours=24,
                      min_team_history=5, minute_window=5, rate_window=10, stale_observation_days=30,
                      limitations=["Observed box-score membership is not a verified pregame roster.",
                                   "Trades become visible only after an earlier new-team box score is available.",
                                   "No injury status is inferred from DNP, absence, or today's summary fields.",
                                   "Minutes are historical estimates, not confirmed lineups or availability.",
                                   "Player identity vocabulary and scalers must be fitted on training data later."], rows=rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.output)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Rotation build failed: {error}", file=sys.stderr)
        return 2
    print(f"Built {report['games']} rotation snapshots; {report['eligible_games']} have enough history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
