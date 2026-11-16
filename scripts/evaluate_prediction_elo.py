"""Compare Elo against saved rolling logistic predictions on identical games."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.elo import compare_elo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("features", type=Path)
    parser.add_argument("rolling", type=Path)
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config/prediction-elo.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/elo")
    args = parser.parse_args()
    try:
        history_raw, feature_raw, rolling_raw = (p.read_bytes() for p in (args.history, args.features, args.rolling))
        history, features, rolling = (json.loads(raw) for raw in (history_raw, feature_raw, rolling_raw))
        if history["coverage"].get("complete") is not True:
            raise ValueError("Historical source is incomplete")
        if features["source_sha256"] != hashlib.sha256(history_raw).hexdigest() or rolling["feature_sha256"] != hashlib.sha256(feature_raw).hexdigest():
            raise ValueError("History, features, and rolling report must have matching provenance")
        report = compare_elo(history["games"], features, rolling, json.loads(args.settings.read_text()))
        report.update(history_sha256=hashlib.sha256(history_raw).hexdigest(),
                      feature_sha256=hashlib.sha256(feature_raw).hexdigest(),
                      rolling_sha256=hashlib.sha256(rolling_raw).hexdigest(),
                      git_revision=subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip(),
                      working_tree_dirty=bool(subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()))
        lines = ["# Elo development benchmark", "", "| Test window | Games | Elo Brier | Logistic Brier | Elo accuracy |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        for fold in report["folds"]:
            lines.append(f"| {fold['name']} | {fold['elo']['games']} | {fold['elo']['brier']:.4f} | {fold['logistic']['brier']:.4f} | {fold['elo']['accuracy']:.1%} |")
        lines.extend(["", "Aggregate:", *[f"{name}: {value}" for name, value in report["aggregate"].items()],
                      "", f"Elo-minus-logistic Brier 95% date-block interval: {report['elo_minus_logistic_brier_95_interval']}",
                      "", *report["limitations"]])
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output / "report.md").write_text("\n".join(lines) + "\n")
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f"Elo evaluation failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
