# Team baseline features

This stage builds historical pregame inputs, not fitted predictions. It uses the
game-level dataset only; player logs, injury statuses, and sportsbook prices are
not model inputs yet.

```bash
.venv/bin/python scripts/build_prediction_features.py \
  artifacts/predictions/history/history-2025-10-21-2025-11-03.json \
  --output artifacts/predictions/features/first-two-weeks.json
```

The builder requires complete collector coverage and checks duplicate IDs, score
consistency, and timezone-aware game times. Completeness is still relative to the
collector's daily source, not an independently verified season schedule.

## Features

| Input | Definition |
| --- | --- |
| Home court | One for an explicitly non-neutral venue; zero for neutral/unknown |
| Unknown neutral status | Separate missing-data indicator |
| Home/away history counts | Number of eligible past games in the current season |
| Win-rate difference | Home minus away, over each team's last ten eligible games |
| Margin difference | Home minus away average point margin, last ten eligible games |
| Recent margin difference | Same calculation over last five eligible games |
| Home/away rest days | Eastern calendar days between games minus one; capped at seven |
| Home/away rest unknown | One when no earlier same-season schedule record exists |
| Home/away back-to-back | One when the prior recorded game was the previous Eastern date |

The target is the current game's home-win outcome, stored separately from the
`features` dictionary. Raw point margins are not opponent- or pace-adjusted and
include overtime. Missing form uses neutral defaults (50% win rate, zero margin),
but rows are marked ineligible until both teams have five usable prior games.
Keep ineligible rows for inspection; exclude them from model fitting/evaluation.

## Time and leakage rules

The cutoff is one hour before scheduled start. An earlier game's result is used
only if its scheduled start plus 24 hours is at or before this cutoff. This is a
documented historical availability assumption, not a verified publication time.
Late corrections can still exist in today's archived box scores. Before claiming
a live-equivalent backtest, audit source timing and test alternative delays.

Rest can use a prior scheduled game whose result is not yet eligible. It does
not use that game's points or outcome. Each row records both result-history IDs
and rest-source IDs so inputs can be traced. Current/future game results never
enter the current row's features. History resets at season boundaries.

Starting collection midway through a season omits previous games and can distort
form/rest. Collect from season opening and retain warmup rows. Schedule changes,
missing games, Cup final classification, and corrected provider records still
require the broader coverage audit described in the source report.

The artifact contains ordered feature names, feature version, source SHA-256,
source coverage, settings, eligibility flags, and limitations. Building features
does not fit a scaler or inspect a test period to select a model.

## Verification and next stage

Synthetic tests cover current/future outcome isolation, delayed result access,
calendar rest, reversed input ordering, season reset, warmup eligibility, malformed
games, neutral sites, and rejection of incomplete datasets.

Local validation on October 21–November 3, 2025: 103 games and 2,313 played-player
records collected without rejections. Feature construction produced 103 rows;
23 meet the five-prior-games-per-team requirement with the default 24-hour delay.
This is enough to exercise the pipeline, not to estimate model accuracy reliably.

Next: collect sufficient earlier-season training history, predeclare chronological
train/validation/test periods, and fit a simple logistic-regression baseline.
Report log loss, Brier score, calibration, and sample counts before experimenting
with player embeddings. The 2025–26 sample is for pipeline validation, not a
final untouched test once it has been repeatedly used for development.
