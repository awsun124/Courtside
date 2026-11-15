# First player encoder

Status: trained offline development model, without a synergy/attention layer or
production API. This experiment adds a small player-based correction to Elo.

## Full-season player data

The [SportsDataverse NBA loaders](https://sportsdataverse-py.sportsdataverse.org/docs/0.0.73/nba/reference/loaders)
identify the public `espn_nba_player_boxscores` release. We import its season CSVs
with the standard library, retaining ESPN IDs, download URLs, retrieval times,
and SHA-256 checksums. File years are season-ending years: 2023 and 2024.

```bash
.venv/bin/python -m pip install -r requirements-ml.txt
.venv/bin/python scripts/import_prediction_player_bulk.py \
  artifacts/predictions/results/multi-season.json
.venv/bin/python scripts/build_prediction_rotations.py \
  artifacts/predictions/players/players-full.json \
  --output artifacts/predictions/players/rotations-full.json
.venv/bin/python scripts/train_prediction_players.py
```

Both imports matched all 2,460 audited regular-season games: dates, team IDs,
final scores, and player scoring totals. They yielded 52,396 numeric played-player
records. All 5,262 numeric logs in the earlier direct-summary sample matched the
bulk records exactly. The bulk provider reports zero minutes for some entries
whose direct summary has unrecorded minutes; preserve source provenance rather
than claim identical missing-data semantics. DNP rows are kept as observations
only. Other source games (playoffs/exhibitions) are filtered by the audited IDs.

All-season rotation construction produced 2,298 eligible matchups after warmup.
The existing development test set still has all 611 games eligible. Estimated
rosters, historical timing assumptions, and trade/injury limitations from the
[player pipeline](prediction-players.md) still apply. Bulk availability today
does not verify original pregame publication times.

## Architecture

```text
Player ID → 8-dimensional learned embedding ─┐
12 lagged per-36 rates + history count ──────┴→ shared MLP → 16-dimensional player vector
                                                           ↓
                                    Sum weighted by projected minutes / 240
                                                           ↓
                                   Home, away, difference + team/rest features
                                                           ↓
                                                Small correction network
                                                           ↓
                                             Elo logit + correction → probability
```

The shared encoder has 32 hidden units and tanh activations. The matchup head has
32 hidden units and dropout 0.1. The final correction starts at zero so the
initial network exactly reproduces Elo. Minute-weighted pooling is invariant to
player order and ignores zero-weight padding. It does not explicitly contextualize
individual player vectors using teammates; that remains the synergy experiment.

Training uses [PyTorch embeddings](https://docs.pytorch.org/docs/2.9/generated/torch.nn.Embedding.html)
and [binary cross-entropy with logits](https://docs.pytorch.org/docs/2.9/generated/torch.nn.BCEWithLogitsLoss.html).
Optional PyTorch dependencies stay out of production `requirements.txt`.

## Evaluation rules

`config/prediction-player-model.json` fixes the first architecture and optimizer
before viewing its test metrics. Reuse the three existing chronological folds,
label-availability embargoes, and validation-selected Elo parameters.

- Build the player-ID vocabulary from training rotation inputs only. ID zero is
  an unknown-player embedding fixed at zero; unknown players retain statistical
  inputs. Do not pre-enumerate players from validation/test.
- Fit rate and team-feature means/scales on training inputs only. Known identity
  embeddings are learned with game outcomes; statistical inputs change over time.
- Train AdamW at learning rate 0.001, weight decay 0.01, batch size 128, gradient
  clipping 1.0. Use CPU deterministic operations and one thread.
- For each fixed seed (42/43/44), retain the epoch with best validation log loss,
  including the unchanged-Elo epoch zero. Maximum 80 epochs, patience 12. Test
  labels cannot select epochs, normalization, vocabulary, or seeds.
- Average all three seed probabilities, with no test-based ensemble weights.
- Compare with the same saved Elo predictions on matching games, checking targets,
  cutoff times, source hashes, and numerical Elo probabilities before fitting.

Unknown player exposures in the December fold are higher because its training
inputs contain little usable 2023–24 history. That is retained rather than solved
by leaking the newer roster into the training vocabulary.

## First observed results

| Test month | Games | Player model Brier | Elo Brier | Player accuracy |
| --- | ---: | ---: | ---: | ---: |
| December 2023 | 207 | 0.1984 | 0.1986 | 66.2% |
| February 2024 | 174 | 0.2194 | 0.2209 | 62.6% |
| March 2024 | 230 | 0.2180 | 0.2182 | 62.2% |

Across 611 test games, player model Brier was 0.211755 versus Elo's 0.212303,
approximately a 0.26% relative reduction. Log loss was 0.606960 versus 0.608215.
Accuracy remained 63.7% for both. Four of nine seed/fold runs selected epoch zero;
the others selected epochs one to three. Validation often rejected learning a
larger correction.

The paired 2,000-resample date-block interval for player-minus-Elo Brier was
[-0.001005, -0.000107]. This is conditional development evidence for a very small
improvement, not model-selection uncertainty or a guarantee of future performance.
The correction includes team features and statistics as well as identity
embeddings, so this comparison alone cannot attribute improvement to embeddings.
Add a no-identity ablation before making that claim. The final untouched period
and sportsbook comparison remain future work.

## Artifacts and tests

Ignored `artifacts/predictions/player-model/` contains the report, per-game
predictions, calibration bins, unknown-player counts, validation loss histories,
training-only preprocessing metadata, and per-fold seed weights. Checkpoints are
lists of tensor state dictionaries; reload locally with `torch.load(...,
weights_only=True)` and matching metadata. The report includes source hashes,
library versions, Git revision, and working-tree state.

Tests cover bulk reconciliation/rejection, unknown IDs, train-only scaling,
pooling-order and padding invariance with a nonzero correction, exact initial
Elo behavior, test-label isolation, and checkpoint reload equivalence. Existing
time-safe rotation/feature tests remain in place. The next work is the controlled
identity ablation and player synergy layer, using validation for design choices
and later reserved data for final claims.
