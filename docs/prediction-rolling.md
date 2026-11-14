# Multi-season rolling baseline

This extends the first development baseline with two earlier complete regular
seasons. The model architecture and candidate regularization values stay the same.
January 2025 remains a documented earlier result, not a tuning target here.

## Dataset

```bash
.venv/bin/python scripts/collect_prediction_results.py --start 2022-10-18 --end 2023-04-09
.venv/bin/python scripts/collect_prediction_results.py --start 2023-10-24 --end 2024-04-14
.venv/bin/python scripts/merge_prediction_results.py \
  artifacts/predictions/results/results-2022-10-18-2023-04-09.json \
  artifacts/predictions/results/results-2023-10-24-2024-04-14.json \
  --full-seasons --output artifacts/predictions/results/multi-season.json
.venv/bin/python scripts/build_prediction_features.py \
  artifacts/predictions/results/multi-season.json \
  --output artifacts/predictions/features/multi-season.json
.venv/bin/python scripts/evaluate_prediction_rolling.py \
  artifacts/predictions/features/multi-season.json
```

The merger rejects incomplete sources, duplicate game IDs, and inconsistent
results. `--full-seasons` additionally requires 1,230 games, 30 teams, and 82 games
per team in every included season. This flag is for standard 82-game seasons;
it is unsuitable for shortened seasons. Matching counts do not replace an
independent schedule cross-check. Raw sources and source hashes are preserved.

The results collector excludes the NBA Cup final and the earlier In-Season
Tournament championship using event notes, as well as explicit postponed/canceled
events and competitions marked `ALLSTAR`. ESPN marked the 2023 All-Star game as
season type 2, so checking season type alone is insufficient. The full-season
count audit exposed this extra game; a regression test now covers its exclusion.
Other unfinished events remain failures. Postponed dates are not used as
played games for form or rest. Completed rescheduled games are included on their
recorded played date when available in the source range.

## Fixed experiment

`config/prediction-rolling.json` was written before collecting these seasons or
viewing their model results. All boundaries use Eastern cutoff dates.

| Fold | Training cutoff | Validation month | Test month |
| --- | --- | --- | --- |
| December 2023 | Before November 2023 | November 2023 | December 2023 |
| February 2024 | Before January 2024 | January 2024 | February 2024 |
| March 2024 | Before February 2024 | February 2024 | March 2024 |

Training expands from the beginning of the collected history. Each fold fits its
own scaler and logistic model using training data only, chooses C on its own
validation log loss, and freezes weights for its test month. Label availability
embargoes apply at boundaries. Earlier fold outcomes can enter later training or
validation once they would have become available; later outcomes cannot affect
earlier models. No test game can appear twice in the aggregate.

Scores are compared with each fold's training-derived home-win probability on
the same test games. The aggregate is calculated from all unique test predictions,
not an unweighted average of fold metrics. Per-fold date-block bootstrap intervals
and calibration bins remain in the JSON report.

## Artifacts and interpretation

Ignored `artifacts/predictions/rolling/` contains a Markdown summary, a JSON report,
and JSON model coefficients/scalers for every fold. Reports preserve configuration,
source hash and coverage, feature assumptions, library versions, Git revision,
working-tree state, per-fold validation trials, and per-game test predictions.

These are development folds. They estimate consistency across periods, not
guaranteed profitability or performance against sportsbooks. The model still
uses team form/rest only; roster and player synergy remain future experiments.
Any changes prompted by these results should use a later, separately reserved
period for final evaluation. Keep negative folds visible alongside positive ones.

## Observed development results

Both seasons passed the count audit after excluding their All-Star games:
1,230 games per season, 30 teams, 82 games per team. The merged dataset has 2,460
games and 2,298 rows with sufficient same-season history. It contains no player
box scores. The second season's original collection process started before the
All-Star exclusion fix, so both seasons were rebuilt from verified raw caches
using the corrected parser before merging and training.

| Test month | Training games | Test games | Model Brier | Home-frequency Brier | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| December 2023 | 1,149 | 207 | 0.2110 | 0.2452 | 64.3% |
| February 2024 | 1,542 | 174 | 0.2312 | 0.2560 | 59.2% |
| March 2024 | 1,769 | 230 | 0.2254 | 0.2593 | 63.0% |

Across 611 unique test games, model accuracy was 62.4% versus 51.6% for the
training-derived constant baseline. Model Brier was 0.2222 versus 0.2536, a 12.4%
relative reduction; log loss was 0.6341 versus 0.7004. Selected C values were
0.1, 0.01, and 0.01 respectively.

Per-fold exploratory 95% date-block bootstrap intervals for model-minus-baseline
Brier difference were [-0.0489, -0.0180], [-0.0443, -0.0060], and
[-0.0500, -0.0180]. These support improvement over this weak constant baseline
within these windows. They do not establish an advantage over an Elo/team-rating
benchmark, sportsbook odds, or a future live sample.

The previous January 2025 result remains negative and is retained in the first
baseline report. The rolling tests use different periods, so the new result is
not a controlled estimate of the effect of increasing training sample size alone.
Next compare against a stronger team-rating baseline and reserve later seasons
for evaluation of future player features.
