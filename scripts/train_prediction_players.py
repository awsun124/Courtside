"""Train the first player encoder on existing chronological development folds."""

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
    parser.add_argument("--history", type=Path, default=PROJECT_ROOT / "artifacts/predictions/results/multi-season.json")
    parser.add_argument("--features", type=Path, default=PROJECT_ROOT / "artifacts/predictions/features/multi-season.json")
    parser.add_argument("--players", type=Path, default=PROJECT_ROOT / "artifacts/predictions/players/players-full.json")
    parser.add_argument("--rotations", type=Path, default=PROJECT_ROOT / "artifacts/predictions/players/rotations-full.json")
    parser.add_argument("--elo", type=Path, default=PROJECT_ROOT / "artifacts/predictions/elo/report.json")
    parser.add_argument("--folds", type=Path, default=PROJECT_ROOT / "config/prediction-rolling.json")
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config/prediction-player-model.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/predictions/player-model")
    args = parser.parse_args()
    try:
        from courtside.predictions.player_model import evaluate_players
        import torch
        import numpy
        raw = {key: getattr(args, key).read_bytes() for key in ("history", "features", "players", "rotations", "elo")}
        data = {key: json.loads(value) for key, value in raw.items()}
        hashes = {key: hashlib.sha256(value).hexdigest() for key, value in raw.items()}
        if (data["features"]["source_sha256"] != hashes["history"] or
                data["players"]["coverage"]["source_sha256"] != hashes["history"] or
                data["rotations"]["source_sha256"] != hashes["players"] or
                data["elo"]["feature_sha256"] != hashes["features"] or
                not data["players"]["coverage"]["full_history_complete"]):
            raise ValueError("Player model inputs do not have matching complete provenance")
        settings = json.loads(args.settings.read_text())
        report, models = evaluate_players(data["history"]["games"], data["features"], data["rotations"],
            json.loads(args.folds.read_text()), data["elo"], settings)
        report.update(source_hashes=hashes, torch_version=str(torch.__version__), numpy_version=numpy.__version__,
                      source_coverage=data["players"]["coverage"],
                      git_revision=subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip(),
                      working_tree_dirty=bool(subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()))
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        for name, artifact in models.items():
            torch.save(artifact["states"], args.output / f"{name}-weights.pt")
            (args.output / f"{name}-metadata.json").write_text(json.dumps({key: value for key, value in artifact.items() if key != "states"}, indent=2) + "\n")
        lines = ["# Player encoder development run", "", "| Test month | Games | Player Brier | Elo Brier | Player accuracy |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        for fold in report["folds"]:
            model, elo = fold["partitions"]["test"]["player_model"], fold["partitions"]["test"]["elo"]
            lines.append(f"| {fold['name']} | {model['games']} | {model['brier']:.4f} | {elo['brier']:.4f} | {model['accuracy']:.1%} |")
        lines.extend(["", *[f"{name}: {metrics}" for name, metrics in report["aggregate"].items()],
                      "", f"Player-minus-Elo Brier 95% date-block interval: {report['player_minus_elo_brier_95_interval']}",
                      "", *report["limitations"]])
        (args.output / "report.md").write_text("\n".join(lines) + "\n")
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        print(f"Player model failed: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
