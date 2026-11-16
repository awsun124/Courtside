"""Evaluate predeclared later-season games with frozen existing checkpoints."""

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
    parser.add_argument("--plan", type=Path, default=PROJECT_ROOT / "config/prediction-holdout.json")
    parser.add_argument("--folder", type=Path, default=PROJECT_ROOT / "artifacts/predictions/holdout")
    args = parser.parse_args()
    try:
        import torch
        from courtside.predictions.holdout import evaluate_holdout, verify_frozen
        plan_raw = args.plan.read_bytes()
        plan = json.loads(plan_raw)
        verify_frozen(PROJECT_ROOT, plan)
        paths = dict(history=args.folder / "history.json", season=args.folder / "results-2024-25.json",
            features=args.folder / "features.json", players=args.folder / "players/players-full.json", rotations=args.folder / "rotations.json")
        raw = {name: path.read_bytes() for name, path in paths.items()}
        hashes = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
        data = {name: json.loads(value) for name, value in raw.items()}
        if (not data["history"]["coverage"]["complete"] or not data["season"]["coverage"]["complete"] or
                not data["players"]["coverage"]["full_history_complete"] or
                data["features"]["source_sha256"] != hashes["season"] or
                data["players"]["coverage"]["source_sha256"] != hashes["season"] or
                data["rotations"]["source_sha256"] != hashes["players"]):
            raise ValueError("Holdout input provenance is incomplete or inconsistent")
        checkpoints = {}
        fold = plan["checkpoint_fold"]
        for name, directory in (("player", "player-model"), ("synergy", "synergy-model")):
            base = PROJECT_ROOT / "artifacts/predictions" / directory
            metadata_path, weights_path = base / f"{fold}-metadata.json", base / f"{fold}-weights.pt"
            if any(str(path.relative_to(PROJECT_ROOT)) not in plan["frozen_files"] for path in (metadata_path, weights_path)):
                raise ValueError("Checkpoint is not included in frozen plan")
            checkpoints[name] = dict(metadata=json.loads(metadata_path.read_text()),
                                     states=torch.load(weights_path, map_location="cpu", weights_only=True))
        elo_path = PROJECT_ROOT / "artifacts/predictions/elo/report.json"
        if str(elo_path.relative_to(PROJECT_ROOT)) not in plan["frozen_files"]:
            raise ValueError("Elo parameters are not frozen")
        elo_report = json.loads(elo_path.read_text())
        parameters = next(f["selected_parameters"] for f in elo_report["folds"] if f["name"] == fold)
        report = evaluate_holdout(data["history"], data["features"], data["rotations"], checkpoints, parameters, plan)
        verify_frozen(PROJECT_ROOT, plan)
        report.update(input_hashes=hashes, plan_sha256=hashlib.sha256(plan_raw).hexdigest(), torch_version=str(torch.__version__))
        lines = ["# Frozen later-season holdout", "", "| Model | Games | Brier | Log loss | Accuracy |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        for name, metrics in report["aggregate"].items():
            lines.append(f"| {name} | {metrics['games']} | {metrics['brier']:.6f} | {metrics['log_loss']:.6f} | {metrics['accuracy']:.1%} |")
        lines.extend(["", *report["limitations"]])
        (args.folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.folder / "report.md").write_text("\n".join(lines) + "\n")
    except (ValueError, KeyError, TypeError, OSError, ImportError, StopIteration) as error:
        print(f"Holdout evaluation failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
