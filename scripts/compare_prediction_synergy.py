"""Compare a completed synergy run with the saved independent-player benchmark."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synergy", type=Path, default=PROJECT_ROOT / "artifacts/predictions/synergy-model/report.json")
    parser.add_argument("--baseline", type=Path, default=PROJECT_ROOT / "artifacts/predictions/player-model/report.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/synergy-comparison")
    args = parser.parse_args()
    try:
        from courtside.predictions.synergy_comparison import compare_synergy
        first, second = args.synergy.read_bytes(), args.baseline.read_bytes()
        synergy, baseline = json.loads(first), json.loads(second)
        report = compare_synergy(synergy, baseline)
        report.update(report_hashes=dict(synergy=hashlib.sha256(first).hexdigest(), baseline=hashlib.sha256(second).hexdigest()),
                      git_revision=synergy["git_revision"], working_tree_dirty=synergy["working_tree_dirty"])
        lines = ["# Player synergy comparison", "", "| Model | Games | Brier | Log loss | Accuracy |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        for name, values in report["aggregate"].items():
            lines.append(f"| {name} | {values['games']} | {values['brier']:.6f} | {values['log_loss']:.6f} | {values['accuracy']:.1%} |")
        lines.extend(["", f"Synergy-minus-baseline Brier 95% date-block interval: {report['synergy_minus_baseline_brier_95_interval']}",
                      "", *report["limitations"]])
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output / "report.md").write_text("\n".join(lines) + "\n")
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f"Synergy comparison failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
