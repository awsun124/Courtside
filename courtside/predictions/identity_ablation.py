"""Paired comparison of otherwise matching identity and no-identity experiments."""

import numpy as np

from .baseline import scores
from .history import EASTERN, timestamp


def compare_identity(identity, no_identity, seed=42):
    if identity["settings"].get("use_identity", True) is not True or no_identity["settings"].get("use_identity", True) is not False:
        raise ValueError("Expected identity and no-identity models")
    ignored = {"experiment", "use_identity"}
    if ({k: v for k, v in identity["settings"].items() if k not in ignored} !=
            {k: v for k, v in no_identity["settings"].items() if k not in ignored}):
        raise ValueError("Ablation settings differ beyond player identity")
    if identity["source_hashes"] != no_identity["source_hashes"]:
        raise ValueError("Ablation input sources differ")
    def indexed(report):
        result = {(row["fold"], row["game_id"]): row for row in report["predictions"]}
        if len(result) != len(report["predictions"]):
            raise ValueError("Duplicate ablation prediction")
        return result
    first, second = indexed(identity), indexed(no_identity)
    if not first or first.keys() != second.keys():
        raise ValueError("Ablation test games differ")
    rows = []
    for key in first:
        a, b = first[key], second[key]
        if any(a[field] != b[field] for field in ("target", "cutoff_at", "elo_probability")):
            raise ValueError("Ablation labels, cutoffs, or Elo reference differ")
        rows.append(dict(game_id=a["game_id"], fold=a["fold"], cutoff_at=a["cutoff_at"], target=a["target"],
                         identity_probability=a["home_probability"], no_identity_probability=b["home_probability"],
                         elo_probability=a["elo_probability"]))
    def metrics(values):
        return {name: scores([r["target"] for r in values], [r[column] for r in values])
                for name, column in (("identity", "identity_probability"), ("no_identity", "no_identity_probability"), ("elo", "elo_probability"))}
    by_date = {}
    for row in rows:
        day = timestamp(row["cutoff_at"]).astimezone(EASTERN).date().isoformat()
        difference = (row["identity_probability"] - row["target"]) ** 2 - (row["no_identity_probability"] - row["target"]) ** 2
        by_date.setdefault(day, []).append(difference)
    blocks, differences = list(by_date.values()), []
    rng = np.random.default_rng(seed)
    for _ in range(2000):
        sample = [value for index in rng.integers(0, len(blocks), size=len(blocks)) for value in blocks[index]]
        differences.append(sum(sample) / len(sample))
    return dict(source_hashes=identity["source_hashes"], aggregate=metrics(rows),
        folds=[dict(name=name, metrics=metrics([r for r in rows if r["fold"] == name])) for name in sorted({r["fold"] for r in rows})],
        identity_minus_no_identity_brier_95_interval=[float(v) for v in np.quantile(differences, [0.025, 0.975])],
        predictions=rows, limitations=["Removing identity also removes its parameters and changes random initialization dimensions.",
                                      "Same fixed seeds, optimizer, epoch rules, data, and Elo reference are retained.",
                                      "Development windows have been inspected repeatedly; this is not a final holdout.",
                                      "No synergy or attention is added in this experiment."])
