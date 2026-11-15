# Match saved odds to ESPN games

From the repository root:

```bash
.venv/bin/python -m courtside.predictions.odds_matching
```

This reads the checksum-verified latest odds snapshot and fetches fresh public
ESPN scoreboards for the upcoming games' Eastern dates. It never loads the odds
API key or calls the odds provider. It saves a timestamped report under ignored
`artifacts/predictions/odds-matches/`. Use `--odds-folder` and `--output` to change
the input and output directories.

Matching uses explicit provider team names mapped to ESPN team IDs, the same
home/away orientation, and start times within 15 minutes. The tolerance allows
the providers' nominal tipoff times to differ; reports retain both times and
their difference. Ambiguous matches, duplicate IDs, and multiple odds events
targeting the same ESPN game are rejected. Unknown names never fuzzy-match.
Only future ESPN events with `STATUS_SCHEDULED` qualify. Postponed, canceled,
in-progress and completed events cannot match.

Reports retain the source snapshot filename/checksum, raw ESPN responses and
retrieval times, matched games, unmatched reasons, and ESPN games without odds.
They preserve each bookmaker's prices and probabilities without averaging or
mixing books. Quotes or snapshots older than six hours are ineligible as current
benchmarks. Future quote timestamps are also ineligible. A matchup may match
while having no eligible quotes. Missing odds remain unavailable.

On November 15, 2026, validation against the saved eight-event snapshot matched
six games and 54 eligible bookmaker quotes. One odds event had started; one had
no qualifying scheduled match. No odds credits were spent by the matcher.

Offline checks:

```bash
.venv/bin/python -m unittest courtside.predictions.test_odds_matching tests.test_prediction_odds
```

The next layer can consume these ESPN-keyed reports to save pregame model
predictions. This command does not yet generate model predictions, expose an API,
build the Predictions page, or schedule collection.
