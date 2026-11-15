# Frozen later-season evaluation

This evaluates existing Elo, independent-player, and synergy models on February
1–April 13, 2025. January 2025 had already been inspected in the first baseline
experiment, so the full 2024–25 season is not described as untouched.

## Plan and frozen models

`config/prediction-holdout.json` was written before collecting/scoring the new
period. It fixes dates, input timing, seed, latest saved checkpoint fold, and
SHA-256 hashes for five local model/metadata/parameter artifacts. The latest
`march-2024` checkpoint was selected by recency, not by the new period's results.
Its earlier training and validation procedures remain unchanged.

The evaluator loads existing seed weights, training-only vocabulary, and scalers.
It does not call training, fit preprocessing, select epochs/seeds, calibrate,
or choose Elo parameters. The existing three seeds are averaged. It verifies all
frozen files before and after evaluation. Changed bytes fail the run.

Elo ratings are replayed from the beginning of the existing 2022–23 history, with
the frozen latest-fold K=20 and home bonus=50. Ratings and lagged input statistics
can update using earlier available outcomes during the holdout, just as they
would for daily predictions. Learned weights and Elo parameters do not change.
The assumed result delay remains 24 hours after scheduled start, with prediction
cutoffs one hour before tipoff.

## Data and reproduction

New collection added 519 completed results for February–April 2025. Combining
them with the existing 711 earlier games yields a 2024–25 season count audit of
1,230 games, 30 teams, and 82 games per team. Combined Elo replay history contains
3,690 games across three seasons.

The public 2025-ending player CSV matched every current-season audited result,
yielding 26,306 numeric player records and 67 explicitly unknown-participation
observations. Full-season input construction yielded 1,147 eligible rows after
warmup. All 519 holdout games were eligible for all models; none were excluded.
Frozen vocabularies left 2,438 of 14,666 player exposures unknown, handled by the
existing zero identity embedding plus statistical inputs.

```bash
.venv/bin/python scripts/collect_prediction_results.py --start 2025-02-01 --end 2025-04-13
.venv/bin/python scripts/merge_prediction_results.py \
  artifacts/predictions/results/results-2024-10-22-2025-01-31.json \
  artifacts/predictions/results/results-2025-02-01-2025-04-13.json \
  --full-seasons --output artifacts/predictions/holdout/results-2024-25.json
.venv/bin/python scripts/merge_prediction_results.py \
  artifacts/predictions/results/multi-season.json \
  artifacts/predictions/holdout/results-2024-25.json \
  --full-seasons --output artifacts/predictions/holdout/history.json
.venv/bin/python scripts/import_prediction_player_bulk.py \
  artifacts/predictions/holdout/results-2024-25.json \
  --output artifacts/predictions/holdout/players
.venv/bin/python scripts/build_prediction_features.py \
  artifacts/predictions/holdout/results-2024-25.json \
  --output artifacts/predictions/holdout/features.json
.venv/bin/python scripts/build_prediction_rotations.py \
  artifacts/predictions/holdout/players/players-full.json \
  --output artifacts/predictions/holdout/rotations.json
.venv/bin/python scripts/evaluate_prediction_holdout.py
```

The original frozen checkpoint files are required to reproduce this exact run.
They are local ignored artifacts, not included in Git. Retraining may produce
different files; do not replace the frozen hashes and call that the original
evaluation. Packaging a versioned model bundle remains deployment work.

## Results

| Frozen model | Games | Brier | Log loss | Accuracy |
| --- | ---: | ---: | ---: | ---: |
| Elo | 519 | 0.203986 | 0.594620 | 68.0% |
| Independent player encoder | 519 | 0.203791 | 0.594191 | 68.0% |
| Player encoder + synergy | 519 | 0.203126 | 0.592552 | 67.8% |

The synergy model had slightly better probability scores but one fewer correct
winner than Elo (352 versus 353). Accuracy is not evidence of improved forecasting
when probability quality is the primary target, and the converse is also not a
profitability claim.

Exploratory 95% paired date-block Brier-difference intervals (2,000 resamples;
negative favors the first model):

| Comparison | Interval |
| --- | --- |
| Player minus Elo | [-0.000297, -0.000098] |
| Synergy minus Elo | [-0.001694, 0.000024] |
| Synergy minus independent player | [-0.001450, 0.000189] |

The independent-player gain is extremely small. Synergy intervals include zero,
so this period does not establish a reliable synergy advantage. Keep these
results alongside the development windows, including the earlier negative
January baseline. Do not retune against this period while continuing to call it
held out. Future architecture changes need a separately reserved period.

## Verification and artifacts

The evaluator checks complete source coverage, input hashes, feature/rotation
timing policies, duplicate rows, matching labels, checkpoint counts, and frozen
artifact hashes. Unit tests check date scope, mutation rejection, policy/source
mismatches, label-independent checkpoint predictions, and unchanged weights and
preprocessing after inference.

Ignored `artifacts/predictions/holdout/report.json` preserves the plan, frozen
hashes, data hashes, per-game predictions, monthly metrics, calibration bins,
unknown-player counts, and uncertainty intervals. A Markdown summary is adjacent.
This is a historical frozen-model evaluation, not prospective live performance.
Historical publication times and injury/trade availability remain approximations;
no sportsbook prices were compared.
