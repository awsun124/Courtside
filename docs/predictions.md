# Predictions

The independent margin-strength ensemble uses Elo, opponent-adjusted scoring
margins, venue and rest. It achieved 375/501 correct winners (74.9%) on February
1–April 12, 2026; Elo achieved 374/501. The probability-score improvement is
small and statistically uncertain. No bookmaker odds enter this model.

[Model, evaluation and reproduction](../courtside/predictions/IMPROVED_MODEL.md)

## Local inference

Install optional research dependencies with
`.venv/bin/pip install -r requirements-ml.txt`. Run commands from the repository root.

```sh
.venv/bin/python -m courtside.predictions.forecast_strength --help
```

The command loads the frozen local checkpoint and complete historical results,
requires a current result collection under six hours old, and writes a new
forecast file. Results become usable 24 hours after scheduled start. Forecasts
require five current-season results per team, recent team data, and generation
at least one hour before tipoff. Inputs contain no verified injuries or lineups.

Generate current independent-model editions with:

```sh
.venv/bin/python -m courtside.predictions.daily_strength
```

This collects public results/schedules and saves today/tomorrow forecasts in
`courtside/artifacts/predictions/availability-daily/`. Set
`COURTSIDE_PREDICTIONS_DIR` to that directory when starting the API to serve the
independent editions. Optional `--odds-folder` attaches saved market comparisons
without requesting odds. The page displays the model methodology and its scoped
historical evaluation. Older snapshots retain their original model identity.
Deployment and automated scheduling remain separate steps.

## Saved odds and forecasts

`ODDS_API_KEY` configures The Odds API live collector; `ODDSPAPI_API_KEY` configures
the separate historical research source. Keep keys in the ignored root `.env`.
The live collector has a six-hour cache and a local monthly attempt cap.

```sh
.venv/bin/python scripts/collect_prediction_odds.py --help
.venv/bin/python -m courtside.predictions.odds_matching --help
.venv/bin/python -m courtside.predictions.daily --help
```

Pass `--matches REPORT.json --forecasts FORECASTS.json` to `daily`. It writes
immutable timestamped snapshots under `artifacts/predictions/daily/YYYY-MM-DD/`.
Missing or late forecasts remain unavailable. Each bookmaker is normalized
separately to remove its two-sided margin. Model-minus-market differences are
absolute probability differences, displayed with a % sign, not expected returns.

`GET /api/predictions?date=YYYY-MM-DD` reads these snapshots for `/predictions`.
Dates use America/New_York. Missing snapshots return an empty result; invalid
dates return 422 and corrupt snapshots return 503. Reads never collect data or
spend credits. The first available forecast for each game is retained with its
paired prices; timestamps and stale states remain visible.

Set `COURTSIDE_PREDICTIONS_DIR` to override the snapshot folder. Artifacts are
ignored by Git; source deployment does not transfer checkpoints or snapshots.
Production persistence and prediction scheduling still need implementation.

## Verification

```sh
.venv/bin/python -m unittest courtside.predictions.test_strength
.venv/bin/python -m unittest discover -s courtside/predictions -t .
```

Historical market comparisons remain local evaluation evidence. The hybrid
experiment selected zero independent-model weight and was abandoned. Its source
was removed; it is not the deployed or independent forecasting model.

## Player availability

The daily producer now checks timestamped official NBA injury-report PDFs and
collects the last ten available player box scores for the relevant teams.
`--base-only` disables this experimental correction. Only confirmed Out players
change probabilities. Questionable/Doubtful statuses are displayed as uncertain.
Missing reports or box scores leave the base forecast intact with an explicit
availability note. Reports marked not yet submitted are never treated as healthy.

The adjustment estimates box-score contribution above a lower-minute replacement
rotation, weighted by actual minutes across the last five team games. Absences
already present in recent form therefore receive diminishing or zero additional
penalty. Each player is capped at four estimated margin points, each team at six.
A fixed 0.10 log-odds shift per margin point converts that difference into a
probability change. These are conservative heuristic constants, not fitted or
validated player-impact estimates. No PPG subtraction or PER claim is made.

January validation selected an injury weight of 0.25 from eight candidates by
winner accuracy, then log loss. On the same 501 February–April games, the weighted
injury model scored 375/501 (74.9%), Brier 0.17856 and log loss 0.53826, compared
with base Brier 0.17951 and log loss 0.54057. There were 327 nonzero adjustments,
79 reviewed/no-change forecasts and 95 without usable availability. All 501 games
remain included. Four winner picks changed, with no net accuracy change.

This is a retrospective comparison on a previously inspected test period, not a
new untouched holdout. The paired Brier-difference interval was [-0.00143,
-0.00045]. The prespecified original unweighted adjustment scored 381/501
(76.0%), Brier 0.17651. It is secondary evidence and did not replace the
validation-selected weight after observing test results.

The prediction job loads the frozen weight from the local injury-evaluation
selection artifact and verifies its inputs. The study module supports initialize,
repair-player-coverage, collect-validation, select, collect-test and test phases.
Official report collection is bounded to eight preceding quarter-hour reports.
The player archive reconciled for 1,223 of 1,230 games after excluding 33
anonymous zero-stat placeholders. Seven inconsistent games also failed direct
source reconciliation, so affected team corrections were withheld.

Local artifacts and the full report are in
`courtside/artifacts/predictions/injury-evaluation/`. Original evaluation files
and checkpoints remain unchanged.
Confirmed starting lineups and automatic daily scheduling remain future work.
Official player names match exactly after normalization within a team, never by
fuzzy last-name matching. Unmatched names and partial coverage remain recorded.
Sources and checksums are cached under the job's availability directory.
