"""Collect/cache NBA moneylines using server-side ODDS_API_KEY."""

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.odds import collect_odds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/odds")
    args = parser.parse_args()
    try:
        snapshot, cached = collect_odds(args.output, args.env_file)
    except (ValueError, KeyError, TypeError, OSError) as error:
        # Provider bodies/credentials are never passed through exceptions.
        print(f"Odds collection failed: {error}", file=sys.stderr)
        return 2
    books = sum(len(game["bookmakers"]) for game in snapshot["games"])
    print(f"{'Reused cached' if cached else 'Saved new'} odds: {len(snapshot['games'])} upcoming games, {books} bookmaker markets.")
    print(f"Retrieved: {snapshot['retrieved_at']}; remaining credits: {snapshot['usage'].get('x-requests-remaining', 'unknown')}")
    print(f"Rejected records: {len(snapshot['rejected'])}; already-started events skipped: {len(snapshot['skipped'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
