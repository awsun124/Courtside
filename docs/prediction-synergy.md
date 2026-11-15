# Player synergy experiment

This adds one teammate self-attention block before projected-minute pooling.
The identity ablation and its results remain intact. This
experiment uses identity embeddings in both its synergy and no-synergy variants;
it does not assume the earlier uncertain identity effect was established.

## Reproduce

```bash
.venv/bin/python scripts/train_prediction_players.py \
  --settings config/prediction-synergy.json \
  --output artifacts/predictions/synergy-model
.venv/bin/python scripts/compare_prediction_synergy.py
```

The comparison defaults to the original saved independent-player report. It
checks source hashes, non-synergy settings, identity mode, test inventories,
labels, cutoffs, and Elo references. Different input data or unmatched games fail
the comparison. Existing player-model training commands still default to no
synergy, preserving their checkpoint structure and random initialization.

## Layer

```text
Player identity + lagged statistical rates
                  ↓
          Shared player encoder
                  ↓
   Masked teammate self-attention (2 heads)
                  ↓
      Residual connection + LayerNorm
                  ↓
    Small feedforward + residual + LayerNorm
                  ↓
      Pool by projected minutes / 240
                  ↓
   Existing matchup head → Elo correction
```

Each player's 16-dimensional representation can depend on the other estimated
players on that team. The same block processes home and away rosters separately.
There is no positional encoding, so input order has no semantic meaning.
[PyTorch's key padding mask](https://docs.pytorch.org/docs/2.9/generated/torch.nn.MultiheadAttention.html)
excludes zero-weight players as attention keys/values. Padded query outputs are
also discarded by the zero-weight pooling. All-masked teams fail explicitly.

This is estimated roster context, not direct observation of which five players
share the court or which opponent guards whom. A low-minute player can still
influence attention if present in the estimate. Actual rotations, injuries,
transactions, and lineup co-occurrence remain data limitations. Attention weights
must not be presented as causal contributions or measured chemistry.

## Training and evaluation

`config/prediction-synergy.json` fixes one attention block with two heads. Other
architecture/training settings match the existing player encoder: three fixed
seeds, AdamW, dropout, maximum epochs, patience, and validation-only checkpoint
selection. The final correction still starts at zero, so epoch zero reproduces
Elo. Player vocabulary, statistical scaling, and label embargoes retain their
training-only rules. Test labels cannot select weights, epochs, or seeds.

Adding the block also adds parameters and changes training randomness. The paired
comparison evaluates this architecture change; it cannot attribute every observed
difference exclusively to basketball interactions independent of model capacity.
Keep later data reserved rather than tune this design against the same test months.

## Observed development results

| Model | Games | Brier | Log loss | Accuracy |
| --- | ---: | ---: | ---: | ---: |
| Player encoder + synergy | 611 | 0.211157 | 0.605944 | 64.2% |
| Independent player encoder | 611 | 0.211755 | 0.606960 | 63.7% |
| Elo | 611 | 0.212303 | 0.608215 | 63.7% |

| Test month | Synergy Brier | Independent-player Brier | Synergy accuracy |
| --- | ---: | ---: | ---: |
| December 2023 | 0.197040 | 0.198426 | 67.6% |
| February 2024 | 0.219640 | 0.219393 | 62.6% |
| March 2024 | 0.217445 | 0.217973 | 62.2% |

The aggregate improvement was small and not uniform: February probability scores
were slightly worse than the independent-player model. A 2,000-resample paired
date-block interval for synergy-minus-baseline Brier was approximately
[-0.001465, 0.000134]. It includes zero, so these development windows do not
establish a reliable synergy improvement. This is not a sportsbook comparison or
final untouched evaluation.

## Artifacts and verification

Ignored `artifacts/predictions/synergy-model/` contains seed/fold checkpoints,
preprocessing, validation curves, and predictions. `synergy-comparison/` contains
paired metrics and report hashes. Code revision and working-tree state are
recorded through the existing training/report pipeline.

Tests cover teammate-dependent representations, order invariance, padded-key
isolation with a nonzero output head, all-masked rejection, valid head dimensions,
the initial Elo anchor, test-label isolation, checkpoint reload, comparison signs,
and source/settings/game mismatches. The full existing prediction suite remains
applicable. The original baseline and no-identity reports are preserved.
