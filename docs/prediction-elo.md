# Elo benchmark

Simple win/loss Elo is now compared with the saved rolling logistic model and
constant home-win baseline on the same 611 test games. This is our own explicit
variant, not a reproduction of FiveThirtyEight's historical NBA model. Public
[NBA Elo data](https://github.com/fivethirtyeight/data/blob/master/nba-elo/README.md)
is background context, not a dataset or parameter source used in this run.

## Reproduce

```bash
.venv/bin/python scripts/evaluate_prediction_elo.py \
  artifacts/predictions/results/multi-season.json \
  artifacts/predictions/features/multi-season.json \
  artifacts/predictions/rolling/report.json
```

Install `requirements-ml.txt` first. The script checks the hashes linking raw
history, features, and the saved logistic report, verifies game targets/cutoffs,
and rejects mismatched or overlapping test sets. No new source requests or model
training jobs are triggered in the web app.

## Rating rules

All teams begin at 1,500. The home-win prediction is:

`p = 1 / (1 + 10 ** ((away_rating - home_rating - home_bonus) / 400))`

The bonus is zero for neutral or unknown venues. Once a result becomes available,
the home rating changes by `K * (home_win - predicted_probability)` and the away
rating changes by the opposite amount. Expected probability is the forecast
recorded before that game, not one recomputed using future ratings.

Updates are queued until scheduled start plus the feature pipeline's assumed
24-hour result delay. Forecasts are made one hour before scheduled tipoff. No
current outcome or unavailable earlier result can affect the current prediction.
At the next season boundary, ratings retain 75% of their deviation from 1,500.
This is a fixed offseason assumption, not a learned roster-change adjustment.

`config/prediction-elo.json` fixes the candidate grid before this benchmark run:
K in 10/20/40 and home bonus in 50/75/100. Each existing rolling fold selects the
pair with lowest validation log loss, never test score. Selected K/bonus pairs
were 40/75, 20/75, and 20/50 across the three folds. Elo ratings continue updating
from available results during validation and test; parameters stay fixed. The
logistic model's coefficients stay frozen while its pregame form inputs likewise
incorporate newly available results.

## Results

| Test month | Games | Elo Brier | Logistic Brier | Elo accuracy |
| --- | ---: | ---: | ---: | ---: |
| December 2023 | 207 | 0.1986 | 0.2110 | 66.2% |
| February 2024 | 174 | 0.2209 | 0.2312 | 62.6% |
| March 2024 | 230 | 0.2182 | 0.2254 | 62.2% |

| Model | Aggregate Brier | Log loss | Accuracy |
| --- | ---: | ---: | ---: |
| Elo | 0.2123 | 0.6082 | 63.7% |
| Logistic regression | 0.2222 | 0.6341 | 62.4% |
| Constant home-win probability | 0.2536 | 0.7004 | 51.6% |

Elo's probability scores were better in all three windows. Accuracy was slightly
lower than logistic in March despite better Brier/log loss. The 2,000-resample
paired date-block bootstrap interval for Elo-minus-logistic Brier was approximately
[-0.0192, -0.00001]. Its upper endpoint is near zero; treat the improvement as
development evidence, not a guarantee of future superiority.

Ignored `artifacts/predictions/elo/` contains JSON and Markdown reports with
validation trials, selected parameters, per-game ratings/probabilities, calibration
bins, source hashes, and code revision/working-tree status. Initial evaluation
ran on an uncommitted working tree, recorded in provenance.

Tests cover neutral/home advantage, delayed updates, rating conservation,
offseason regression, ordering, current/future result isolation, validation-only
selection, and exact test-game matching. This model has no margin multiplier,
player inputs, injury adjustments, or sportsbook comparison. The next model work
can now add player representations and synergy against this stronger reference;
keep these periods labeled development and reserve later data for final evaluation.
