# Daily pregame snapshots

Run from the repository root using a report from the matchup matcher:

```bash
.venv/bin/python -m courtside.predictions.daily \
  --matches artifacts/predictions/odds-matches/REPORT.json
```

The default date is today in America/New_York. `--date YYYY-MM-DD` selects an
Eastern date, and `--output` overrides `artifacts/predictions/daily/`. Each run
saves a new timestamped file inside the date directory; existing files cannot be
overwritten. Started games are excluded. The job reads local files only, spends
no odds credits, and is not scheduled automatically.

Each scheduled game includes ESPN team/game IDs, tipoff, a one-hour-before-tipoff
cutoff, explicit model and market availability, and individual bookmaker prices.
Games without matched markets remain present. Missing forecasts have null
probabilities and an explanation; no sample probabilities are substituted.
Source file SHA-256 hashes identify the exact matching and forecast inputs.

## Forecast producer contract

Pass `--forecasts FILE.json` to attach real current-input model outputs:

```json
{
  "model_version": "versioned-model-artifact-id",
  "generated_at": "2026-11-15T20:00:00+00:00",
  "predictions": [{
    "game_id": "espn:123",
    "home_team_id": "espn:2",
    "away_team_id": "espn:13",
    "scheduled_start": "2026-11-15T22:00:00+00:00",
    "cutoff_at": "2026-11-15T21:00:00+00:00",
    "generated_at": "2026-11-15T20:00:00+00:00",
    "inputs_as_of": "2026-11-15T19:59:00+00:00",
    "home_probability": 0.6
  }]
}
```

This is a synthetic schema example, not a live forecast. The away probability is
the complement. Forecasts require unique ESPN IDs, matching home/away IDs and
exact scheduled start/cutoff timestamps, finite probabilities strictly inside
(0,1), and input availability no later than generation. Future-generated files
are rejected. Forecasts generated or first saved after the cutoff remain
unavailable. Early snapshots are labeled by their actual saved/input times;
they are not described as exact-cutoff forecasts.

Match reports and schedule sources must be at most six hours old. Quotes must
have been retrieved before report generation, be at most six hours old, and be
saved by the game's cutoff to qualify as a benchmark. Each bookmaker retains its
own prices, break-even and margin-adjusted probabilities. The home probability
difference is in percentage points and is computed only when both forecasts and
quotes are eligible. It is not a profitability estimate.

## Forecast producers

The [live Elo producer](LIVE_ELO.md) collects current-season results and generates
real baseline forecasts directly into this snapshot layer.

The historical checkpoint evaluators do not yet produce current-game features
and forecasts. This layer accepts such a producer's explicit outputs but does
not train a model or convert old historical predictions into current forecasts.
Without `--forecasts`, it saves market observations with honest model-unavailable
states. Saved-result API/page integration and outcome evaluation are next.

```bash
.venv/bin/python -m unittest courtside.predictions.test_daily \
  courtside.predictions.test_odds_matching tests.test_prediction_odds
```
