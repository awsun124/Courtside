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

The current UI still displays the earlier season-reset Elo snapshots. The new
model has not been connected to the default daily producer or deployed.

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
