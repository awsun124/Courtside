"""Run predefined multi-season rolling baseline experiments."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.rolling import evaluate_rolling


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("features", type=Path)
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config/prediction-rolling.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/rolling")
    args = parser.parse_args()
    try:
        raw = args.features.read_bytes()
        dataset = json.loads(raw)
        report, models = evaluate_rolling(dataset, json.loads(args.settings.read_text()))
        report.update(feature_sha256=hashlib.sha256(raw).hexdigest(),
                      source_coverage=dataset["source_coverage"],
                      feature_version=dataset["feature_version"],
                      feature_limitations=dataset["limitations"],
                      git_revision=subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip(),
                      working_tree_dirty=bool(subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()))
        import sklearn
        import numpy
        report.update(sklearn_version=sklearn.__version__, numpy_version=numpy.__version__)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output / "models.json").write_text(json.dumps(dict(feature_sha256=report["feature_sha256"], models=models), indent=2) + "\n")
        lines = ["# Rolling baseline development run", "", "| Test window | Training games | Test games | Model Brier | Home-only Brier | Model accuracy |",
                 "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for fold in report["folds"]:
            model = fold["partitions"]["test"]["model"]
            home = fold["partitions"]["test"]["home_frequency"]
            lines.append(f"| {fold['name']} | {fold['partitions']['train']['model']['games']} | {model['games']} | {model['brier']:.4f} | {home['brier']:.4f} | {model['accuracy']:.1%} |")
        model, home = report["aggregate"]["model"], report["aggregate"]["home_frequency"]
        lines.extend(["", f"Aggregate: {model['games']} test games; model Brier {model['brier']:.4f}, baseline {home['brier']:.4f}.",
                      "", *report["limitations"]])
        (args.output / "report.md").write_text("\n".join(lines) + "\n")
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f"Rolling evaluation failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
