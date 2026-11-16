"""Paired comparison of roster-attention and independent-player models."""

import numpy as np

from .baseline import scores
from .history import EASTERN, timestamp


def compare_synergy(synergy, baseline, seed=42):
    if synergy["settings"].get("use_synergy", False) is not True or baseline["settings"].get("use_synergy", False) is not False:
        raise ValueError("Expected synergy and no-synergy models")
    ignored = {"experiment", "notes", "use_synergy", "synergy_heads"}
    normalized = [{**{k: v for k, v in r["settings"].items() if k not in ignored},
                   "use_identity": r["settings"].get("use_identity", True)} for r in (synergy, baseline)]
    if normalized[0] != normalized[1] or synergy["source_hashes"] != baseline["source_hashes"]:
        raise ValueError("Synergy comparison settings or inputs do not match")
    indices = []
    for report in (synergy, baseline):
        indexed = {(r["fold"], r["game_id"]): r for r in report["predictions"]}
        if len(indexed) != len(report["predictions"]):
            raise ValueError("Duplicate synergy comparison game")
        indices.append(indexed)
    if not indices[0] or indices[0].keys() != indices[1].keys():
        raise ValueError("Synergy comparison test inventories differ")
    rows = []
    for key, first in indices[0].items():
        second = indices[1][key]
        if any(first[field] != second[field] for field in ("target", "cutoff_at", "elo_probability")):
            raise ValueError("Synergy comparison labels, cutoffs, or Elo differ")
        rows.append(dict(game_id=first["game_id"], fold=first["fold"], cutoff_at=first["cutoff_at"],
                         target=first["target"], synergy_probability=first["home_probability"],
                         no_synergy_probability=second["home_probability"], elo_probability=first["elo_probability"]))
    def metrics(values):
        return {name: scores([r["target"] for r in values], [r[column] for r in values]) for name, column in (
            ("synergy", "synergy_probability"), ("no_synergy", "no_synergy_probability"), ("elo", "elo_probability"))}
    by_date = {}
    for row in rows:
        day = timestamp(row["cutoff_at"]).astimezone(EASTERN).date().isoformat()
        difference = (row["synergy_probability"] - row["target"]) ** 2 - (row["no_synergy_probability"] - row["target"]) ** 2
        by_date.setdefault(day, []).append(difference)
    blocks, differences = list(by_date.values()), []
    rng = np.random.default_rng(seed)
    for _ in range(2000):
        sample = [value for index in rng.integers(0, len(blocks), size=len(blocks)) for value in blocks[index]]
        differences.append(sum(sample) / len(sample))
    return dict(source_hashes=synergy["source_hashes"], aggregate=metrics(rows), predictions=rows,
        folds=[dict(name=name, metrics=metrics([r for r in rows if r["fold"] == name])) for name in sorted({r["fold"] for r in rows})],
        synergy_minus_baseline_brier_95_interval=[float(v) for v in np.quantile(differences, [0.025, 0.975])],
        limitations=["Attention models estimated roster context, not observed simultaneous five-player lineups.",
                     "Added parameters and training randomness also differ from the independent-player architecture.",
                     "Attention weights do not establish causal player contributions or chemistry.",
                     "Repeated development windows, not a final holdout or sportsbook comparison."])
