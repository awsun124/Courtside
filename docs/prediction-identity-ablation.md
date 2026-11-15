# Does player identity help?

This experiment compares the same player-stat model with and without learned
identity embeddings. It adds no synergy/attention layer and makes no changes to
data collection, estimated rotations, Elo parameters, or evaluation windows.

## Reproduce

```bash
.venv/bin/python scripts/evaluate_prediction_identity.py
```

The runner derives two settings files from `config/prediction-player-model.json`,
changing only `use_identity`, and runs the existing training CLI in separate
output folders. The default model retains the original embedding behavior;
`use_identity=false` removes the embedding module and its encoder input columns.
Player IDs cannot affect that branch's forward pass. The training-only vocabulary
is retained as diagnostic metadata but is not used by the no-identity network.

Both variants retain the same rate/context scaling procedure, projected-minute
pooling, Elo correction, seeds 42/43/44, optimizer, validation checkpoint rule,
and seed averaging. Each variant may select different epochs using validation
log loss, including the unchanged-Elo epoch zero. No architecture or seed is
selected using test outcomes.

Removing identity also reduces parameter count and changes random-initialization
dimensions. Fixed seeds do not make the two architectures' initial weights
identical. This is a conventional component ablation, not an exact causal estimate
of player identity independent of capacity or initialization.

## Matching and provenance

The comparison rejects different input hashes, changed hyperparameters, duplicate
games, different test inventories, labels, cutoffs, or Elo reference predictions.
The identity-enabled rerun reproduced every previously saved test prediction
exactly. All 611 existing development test games were compared.

Ignored `artifacts/predictions/identity-ablation/` contains per-variant settings,
reports, checkpoints, and preprocessing artifacts, plus a combined JSON/Markdown
comparison. The combined report records settings/source hashes, variant report
hashes, Git revision, working-tree state, and paired predictions.

## Observed results

| Model | Games | Brier | Log loss | Accuracy |
| --- | ---: | ---: | ---: | ---: |
| Player stats + identity embeddings | 611 | 0.211755 | 0.606960 | 63.7% |
| Player stats without identity | 611 | 0.211870 | 0.607269 | 64.0% |
| Elo | 611 | 0.212303 | 0.608215 | 63.7% |

| Test month | Identity Brier | No-identity Brier |
| --- | ---: | ---: |
| December 2023 | 0.198426 | 0.198289 |
| February 2024 | 0.219393 | 0.219688 |
| March 2024 | 0.217973 | 0.218179 |

Identity improved aggregate probability scores by a very small amount, but the
no-identity variant had better accuracy. A 2,000-resample paired date-block
bootstrap interval for identity-minus-no-identity Brier was approximately
[-0.000333, 0.000091]. It includes zero. These development results do not establish
a clear benefit from learned player identity. They also do not establish that
identity is useless in larger datasets or other architectures.

The development windows have now been inspected repeatedly. Do not present them
as a final untouched holdout or infer profitability. Next add the player synergy
layer as a separate experiment and retain both identity variants as references;
use a later reserved period for final claims.

The [synergy experiment](prediction-synergy.md) is now implemented separately,
retaining this ablation and its results as references.

## Verification

Tests exercise identity-independent predictions with a nonzero correction head,
absence of identity parameters, compatibility with original checkpoints, paired
comparison signs, and rejection of changed settings/sources/games/labels/cutoffs.
The existing training-only preprocessing, test-label isolation, and checkpoint
reload tests remain in place.
