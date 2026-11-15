# Live current-season Elo baseline

From the repository root:

```bash
.venv/bin/python -m courtside.predictions.odds_matching
.venv/bin/python -m courtside.predictions.live_elo \
  --matches artifacts/predictions/odds-matches/REPORT.json
```

This CLI fetches public ESPN scoreboards from October 1 through yesterday in
Eastern time, preserving raw responses, URLs and retrieval times. It validates
final regular-season results. Missing source days, invalid scores, duplicates,
or unresolved games older than 48 hours fail the run. Postponed, canceled,
preseason, postseason, All-Star and identified Cup finals are excluded.
There is one scoreboard request per date and no per-game or Odds API requests.
The supported window is October-June, capped at 250 elapsed days. Each run
currently refetches the season inventory; no automatic schedule or retry exists.

## Model policy

`elo-current-season-reset-v1` is a separate live baseline. Teams start at 1500
this season. It does not reproduce multi-season benchmark ratings or the
player/synergy models. Fixed K=20, home bonus=50 and scale=400 come from the
existing March 2024 validation fold. The CLI verifies the frozen Elo report's
SHA-256 against the holdout plan before loading parameters. No training, tuning
or selection on live games occurs.

Historical expected probabilities use the existing delayed-update Elo model.
Only results at least 24 hours after scheduled start affect current ratings,
and collection must have completed before forecast generation. Teams need five
available current-season results, including one within ten days. Neutral or
unknown venues receive no home bonus. Forecasts cover today/tomorrow and must
be generated and saved by one hour before ESPN tipoff. Early forecasts retain
their actual generation/input times. Upcoming records contain no outcomes or
fabricated scores; IDs overlapping history are rejected.

This model has no player, roster or injury inputs. Its current-season reset
policy requires prospective evaluation; historical benchmark scores do not
measure this live variant, and no profitability claim is made.

## Output and verification

Ignored `artifacts/predictions/live-elo/TIMESTAMP/` stores immutable history and
forecasts, with history, matchup-report and frozen-parameter hashes. The producer
feeds forecasts directly into the [daily snapshot layer](DAILY.md). Override
locations with `--output` and `--daily-output`.

On November 15, 2026, the first run collected 188 final results from 45 daily
scoreboards, generated five forecasts and excluded two events whose cutoffs
had passed. The saved daily file contained six upcoming games, five model
forecasts and 45 eligible bookmaker comparisons. No odds credits were spent.
Forecast outcomes have not yet been evaluated.

```bash
.venv/bin/python -m unittest courtside.predictions.test_live_elo \
  courtside.predictions.test_daily courtside.predictions.test_odds_matching \
  tests.test_prediction_odds tests.test_prediction_elo
```

Next: expose saved snapshots through a read-only API and Predictions page,
then evaluate recorded forecasts after results become available.
