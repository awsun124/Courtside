# First team baseline

Status: offline development benchmark, not a production prediction service.

## Reproduce

Install optional research dependencies into the project virtual environment:

```bash
.venv/bin/python -m pip install -r requirements-ml.txt
.venv/bin/python scripts/collect_prediction_results.py --start 2024-10-22 --end 2025-01-31
.venv/bin/python scripts/build_prediction_features.py \
  artifacts/predictions/results/results-2024-10-22-2025-01-31.json \
  --output artifacts/predictions/features/baseline-development.json
.venv/bin/python scripts/train_prediction_baseline.py \
  artifacts/predictions/features/baseline-development.json
```

Production web requirements do not include scikit-learn. The lightweight results
collector reads daily scoreboards, reuses raw caches, and explicitly excludes
non-regular-season events, the NBA Cup final identified by its event note, and
canceled/postponed events. Other unfinished games are failures. Completeness is
relative to source listings; no independent schedule verification is implied.

## Experiment design

Settings are versioned in `config/prediction-baseline.json` before inspecting the
first model results. The split uses Eastern cutoff dates:

- Training: before December 1, 2024, with source coverage starting October 22.
- Validation: December 2024.
- Test: January 2025.

Features require five available prior games per team. Labels used for training
or model selection must clear the assumed availability delay before the next
partition starts; boundary games that do not clear this embargo are excluded.

The pipeline fits StandardScaler and L2 logistic regression on training rows
only. Select C from 0.01, 0.1, and 1 using validation log loss. Freeze the selected
model for testing; do not refit on validation. Compare the same games against
a constant home-win probability estimated on training data. The baseline does
not update its home-win probability using validation or test outcomes.

Report Brier score, log loss, accuracy, and ten probability calibration bins.
A paired bootstrap resamples test dates 2,000 times for the difference in Brier
score (model minus baseline). Negative differences favor the model. This
exploratory interval does not capture every source of uncertainty or dependence.

References: [logistic regression](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html),
[training pipeline](https://scikit-learn.org/stable/modules/generated/sklearn.pipeline.Pipeline.html).

## First observed results

Collection validated 711 final results across 102 calendar dates. It explicitly
excluded five postponed events and one NBA Cup final. No player box scores were
required. Features yielded 628 rows with enough history; 617 entered the three
partitions after the boundary-label embargo. Selected C was 0.1.

| Partition | Games | Model Brier | Home-frequency Brier | Model log loss | Model accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train | 205 | 0.1924 | 0.2357 | 0.5710 | 72.2% |
| Validation | 185 | 0.2190 | 0.2623 | 0.6234 | 63.2% |
| Test | 227 | 0.2911 | 0.2553 | 0.8112 | 51.1% |

Test home-frequency baseline accuracy was 53.7% and log loss was 0.7043. The
learned model performed worse on all three test metrics. The approximate test
Brier-difference interval was [-0.0008, 0.0702], offering no evidence of improvement.
This run is a benchmark to improve, not evidence of useful predictive advantage.
Do not repeatedly tune on January and continue calling it an untouched test.

## Artifacts and limitations

Ignored `artifacts/predictions/baseline/` holds `report.json`, `report.md`, and
`model.json`. The report retains per-game validation/test probabilities,
calibration bins, candidate validation metrics, split dates, source feature hash,
library versions, Git revision, and whether the working tree was dirty. The JSON
model stores scaler means/scales, coefficients, and intercept without pickle.
The initial run used an uncommitted working tree, recorded in its provenance.

Tests verify split disjointness, label embargoes, train-only scaling, independence
of model selection from test labels, and prediction equivalence of exported
coefficients. Existing collector/feature tests remain applicable.

The training sample is small (205 games), team margins are unadjusted, historical
publication timing is assumed, and the model has no roster or injury inputs.
No market odds were used, so this experiment says nothing about beating prices.
Next collect earlier seasons for training and use predefined rolling validation
windows. Retain this January run as a documented development result and reserve
a later period for any final evaluation after architecture choices are settled.
