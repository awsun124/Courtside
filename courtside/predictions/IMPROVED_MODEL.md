# Margin-strength model research

Research completed November 15, 2026. This document records the original
team-based model study. The deployed model now includes player-availability
adjustments. See [the prediction guide](../../docs/predictions.md) for current behavior.

## What was built

`strength.py` constructs pregame features from final team scores, with the same
one-hour prediction cutoff and assumed 24-hour result delay as the existing Elo.
It includes:

- The existing multi-season Elo, reproduced to floating-point precision.
- Three adaptive score-margin ratings with different update speeds, clipped
  residual updates, and 65% offseason retention.
- Opponent-adjusted ridge strengths with 60/120/240-day exponential half-lives,
  ten pseudo-games of shrinkage, clipped historical margins, and a fixed
  three-point venue adjustment. These ratings use earlier results only.
- Venue, rest, back-to-backs and differences in lagged team scoring/form.

The selected model blends two regularized score-margin regressions and an
adaptive margin rating. Normal-CDF transforms convert predicted margins into win
probabilities. Final weights are 56.25% ridge(alpha=100, sigma=12), 25%
ridge(alpha=30, sigma=13.5), and 18.75% adaptive margin(rate=.06, sigma=15).
Both regressions use strength/venue/rest inputs. They exclude the extra short-term
form features because validation favored the smaller feature set.

There are no market, injury, confirmed-lineup or player inputs. More complicated
boosted trees were tested and performed worse. This is the strongest candidate
under the declared validation criterion, not a claim of a universally best model.

## Selection and final evaluation

The tracked `research_plan.json` reserved February 1–April 12, 2026 before any
experimentation. Previously inspected 2025 and January 2026 results were explicitly
reclassified as development data.

Four expanding chronological folds cover November 2023–April 2024, November
2024–January 2025, February–April 2025, and November 2025–January 2026. Each fold
fits scaling and supervised weights using only labels available before the fold.
Result-driven rating updates remain allowed after the fixed delay. This follows
the time-ordering principle in the [scikit-learn cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html#time-series-split)
and the training-only preprocessing guidance in [common pitfalls](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

The search tested 76 initial candidates and 24 refinements (100 total), including
Elo, direct margin-to-probability mappings, logistic regression, ridge margin
regression, conservative boosted trees, and blends. Selection used pooled
validation log loss across 2,943 games. Refinement improved log loss by less than
0.0001; the search then stopped. It did not optimize for a 70% accuracy headline.

Validation results: selected Brier 0.21113 versus Elo 0.21536; log loss 0.60953
versus 0.61887; accuracy 65.9% versus 65.4%. Both probability metrics improved in
each of the four development folds, but these are model-selection results.

The final checkpoint was fitted on 4,088 eligible earlier games with labels
available by February 1, 2026. The selection, weights, implementation and inputs
were hashed before collecting final-test results. The 501 final games plus 729
earlier current-season games reconcile to 1,230 regular-season games. All 501
were eligible; none were filtered by confidence or subsequent outcome.

| Method | Correct | Accuracy | Brier | Log loss |
| --- | ---: | ---: | ---: | ---: |
| Selected margin-strength blend | 375/501 | 74.9% | 0.17951 | 0.54057 |
| Previous multi-season Elo | 374/501 | 74.7% | 0.18270 | 0.54796 |
| Current season-reset Elo | 372/501 | 74.3% | 0.18403 | 0.55155 |

The selected model's Brier is 1.74% lower than multi-season Elo, with only one
additional correct winner. The paired 2,000-resample date-block 95% interval for
the Brier difference is [-0.00778, +0.00126], which includes zero. This is a
promising candidate, not a statistically established improvement. The 74.9% and
earlier January 64.5% figures concern different games and cannot be interpreted
as a ten-point model improvement.

## Existing January market diagnostic

The original 141 eligible FanDuel games are retained exactly. The new model uses
the corresponding out-of-time development predictions, trained before November
2025. The final checkpoint, which was trained on January results, is deliberately
not used for this comparison.

| Method | Accuracy | Brier | Log loss |
| --- | ---: | ---: | ---: |
| Selected model | 58.2% | 0.2308 | 0.6563 |
| Multi-season Elo | 64.5% | 0.2261 | 0.6460 |
| FanDuel, margin removed | 64.5% | 0.2131 | 0.6161 |

The model was worse on this market-covered subset. It assigned more probability
than the market to the actual winner in 57/141 games (40.4%); this is not profit
or return. No fresh February–April sportsbook comparison was collected. January
coverage remains 141/233 games and is development evidence, not a fresh holdout.
Keep this model experimental rather than claiming it beats sportsbooks.

## Files and reproduction

Run from the repository root with the optional `requirements-ml.txt` environment.
The source histories are the existing complete `artifacts/predictions/holdout/history.json`
and `artifacts/predictions/fanduel-2026-01/results/results-2025-10-01-2026-01-31.json`.
The commands below already ran; immutable output guards reject overwriting their
results. A reproduction needs new output directories. Repeating this study does
not make its final period untouched again.

```bash
.venv/bin/python -m courtside.predictions.improve develop
.venv/bin/python -m courtside.predictions.refine_strength
.venv/bin/python scripts/collect_prediction_results.py \
  --start 2026-02-01 --end 2026-04-12 \
  --output courtside/artifacts/predictions/improved-v2/final-results
.venv/bin/python -m courtside.predictions.improve test \
  --output courtside/artifacts/predictions/improved-v2 \
  --history courtside/artifacts/predictions/improved-v2/final-results/results-2026-02-01-2026-04-12.json
.venv/bin/python -m courtside.predictions.research_report
.venv/bin/python -m unittest courtside.predictions.test_strength
```

Ignored local output `courtside/artifacts/predictions/improved-v2/` contains
`selection.json`, `model.joblib`, `plan.json`, per-game development and final
predictions, calibration, monthly metrics, source checksums, the final result
cache, `test-predictions.csv`, `environment.json`, and `report.md`. The original
76-candidate search is preserved separately in `improved-v1/`.

For local inference, `python -m courtside.predictions.forecast_strength --help`
describes the frozen model, repeatable history arguments, saved matches, and
new output filename. It needs complete older histories plus a current history
collection under six hours old. It enforces the one-hour cutoff, tomorrow-only
horizon, minimum five current-season results/team, and ten-day team freshness.
It writes a separate compatible forecast artifact; it does not update the API,
daily snapshots, production or the UI. Load only these trusted local joblib files.

Tests cover current/future-result isolation, exact delay boundaries, warmup,
season transitions, Elo equivalence, order invariance, training-label embargo,
checkpoint round trips, future-trained-model rejection, fixture deadlines, and
market quote/target alignment. Independent single-fixture replays verify saved
final predictions without exposing those fixtures' outcomes to inference.

Further tuning must use another reserved evaluation period or prospective
forecasts. Reusing these final scores to choose a different winner would turn
them into additional development data. Meaningful new research would add
audited pregame player availability rather than endlessly tune this same search.
