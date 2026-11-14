"""Fit and evaluate an offline team baseline using predefined chronological splits."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtside.predictions.baseline import fit_baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("features", type=Path)
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config/prediction-baseline.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/baseline")
    args = parser.parse_args()
    try:
        raw = args.features.read_bytes()
        report, artifact = fit_baseline(json.loads(raw), json.loads(args.settings.read_text()))
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                                  capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
                                   capture_output=True, text=True, check=True).stdout.strip())
        provenance = dict(feature_sha256=hashlib.sha256(raw).hexdigest(), git_revision=revision,
                          working_tree_dirty=dirty)
        report.update(provenance)
        artifact.update(provenance)
        args.output.mkdir(parents=True, exist_ok=True)
        for filename, value in (("report.json", report), ("model.json", artifact)):
            (args.output / filename).write_text(json.dumps(value, indent=2) + "\n")
        lines = ["# Team baseline development run", "", "| Partition | Games | Model Brier | Home-only Brier | Model log loss | Model accuracy |",
                 "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for name, values in report["partitions"].items():
            model, baseline = values["model"], values["home_frequency"]
            lines.append(f"| {name} | {model['games']} | {model['brier']:.4f} | {baseline['brier']:.4f} | {model['log_loss']:.4f} | {model['accuracy']:.1%} |")
        lines.extend(["", f"Selected C: {report['selected_C']} (validation log loss).",
                      f"Test Brier difference 95% date-block bootstrap interval: {report['test_brier_difference_95_interval']}.",
                      "Negative differences favor the model; intervals are exploratory.", ""] + report["limitations"])
        (args.output / "report.md").write_text("\n".join(lines) + "\n")
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f"Baseline failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines[:9]))
    print(f"Artifacts: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
