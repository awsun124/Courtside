"""Import complete historical player seasons from public bulk CSVs."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.player_bulk import cache_csv, import_bulk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/players")
    args = parser.parse_args()
    try:
        raw = args.history.read_bytes()
        history = json.loads(raw)
        years = sorted({int(g["season"][:4]) + 1 for g in history["games"]})
        paths, sources = [], []
        for year in years:
            path, metadata = cache_csv(year, args.output)
            paths.append(path)
            sources.append(metadata)
        dataset = import_bulk(history, paths, sources)
        dataset["coverage"]["source_sha256"] = hashlib.sha256(raw).hexdigest()
        destination = args.output / "players-full.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(dataset, indent=2) + "\n")
        temporary.replace(destination)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Bulk import failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(dataset["coverage"], indent=2))
    return 0 if dataset["coverage"]["selected_complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
