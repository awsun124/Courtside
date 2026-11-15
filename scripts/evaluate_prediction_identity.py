"""Run matched identity/no-identity player models and compare their predictions."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config/prediction-player-model.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/identity-ablation")
    args = parser.parse_args()
    try:
        from courtside.predictions.identity_ablation import compare_identity
        base_raw = args.settings.read_bytes()
        base = json.loads(base_raw)
        args.output.mkdir(parents=True, exist_ok=True)
        reports = {}
        for variant, enabled in (("identity", True), ("no_identity", False)):
            settings = {**base, "use_identity": enabled}
            settings_path = args.output / f"{variant}-settings.json"
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            folder = args.output / variant
            print(f"Training {variant}", flush=True)
            result = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts/train_prediction_players.py"),
                "--settings", str(settings_path.resolve()), "--output", str(folder.resolve())],
                cwd=PROJECT_ROOT, capture_output=True, text=True)
            if result.returncode:
                raise ValueError(result.stderr.strip() or "Player training failed")
            reports[variant] = json.loads((folder / "report.json").read_text())
        report = compare_identity(reports["identity"], reports["no_identity"])
        report.update(base_settings_sha256=hashlib.sha256(base_raw).hexdigest(),
            variant_report_hashes={name: hashlib.sha256((args.output / name / "report.json").read_bytes()).hexdigest() for name in reports},
            git_revision=reports["identity"]["git_revision"], working_tree_dirty=reports["identity"]["working_tree_dirty"])
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        lines = ["# Player identity ablation", "", "| Model | Games | Brier | Log loss | Accuracy |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        for name, values in report["aggregate"].items():
            lines.append(f"| {name} | {values['games']} | {values['brier']:.6f} | {values['log_loss']:.6f} | {values['accuracy']:.1%} |")
        lines.extend(["", f"Identity-minus-no-identity Brier 95% date-block interval: {report['identity_minus_no_identity_brier_95_interval']}",
                      "", *report["limitations"]])
        (args.output / "report.md").write_text("\n".join(lines) + "\n")
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f"Identity ablation failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
