# Historical player pipeline

This stage produces lagged player statistics and estimated rotations for later
embedding experiments. It does not train embeddings, interaction layers, or a
new matchup model. Existing Elo/logistic evaluation results are unchanged.

## Collect

```bash
.venv/bin/python scripts/collect_prediction_players.py \
  artifacts/predictions/results/multi-season.json --games-per-season 120
.venv/bin/python scripts/build_prediction_rotations.py \
  artifacts/predictions/players/players-prefix-120.json \
  --output artifacts/predictions/players/rotations-prefix-120.json
```

The collector selects the first N chronological games of every source season,
starting at season opening. The default 120 per season is a bounded pipeline
validation batch, not full-season player coverage. Increasing the same argument
resumes from the raw cache; 1,230 requests each full season. Only uncached requests
pause for one second. Provider failures are recorded without losing successful
games; rerun to retry missing requests. A malformed cached response must be
reviewed before choosing a new output folder to refetch it.

Raw summaries are checksum-verified, with URL/retrieval provenance. Parsed player
logs reconcile to each game's final score. Summary dates, season, team IDs, and
scores must match the audited game dataset. IDs retain their `espn:` namespace;
no name-based NBA ID crosswalk is invented. Prefix data can be joined to our ESPN
game results directly, but other providers still need explicit mappings.

Coverage distinguishes `selected_complete` (every requested prefix game validated)
from `full_history_complete` (all source games collected). The rotation builder
refuses incomplete selected coverage. It only constructs rows for games inside
the collected prefix, avoiding forecasts that silently skip intervening history.
Game/player duplicates and inconsistent played-player observations fail validation.

## Observations, not historical injury statuses

Two separate records are saved:

- Played-player logs: minutes, points, rebounds, assists, turnovers, steals,
  blocks, and made/attempted field goals, three-pointers, and free throws.
- Box-score observations: player ID, team ID, game ID, and DNP indicator, including
  listed players who did not play. DNP reasons and current injury fields are not used.

If minutes are `--` with all-zero/unrecorded stats despite `didNotPlay=False`,
retain a membership observation with `participation_unknown=true` and omit the
numeric played log. Do not invent a duration or reclassify that observation as an
injury/DNP. Nonzero stats with missing minutes still reject the game. Coverage
counts unknown-participation observations explicitly. Parser version 2 introduces
this handling; existing raw responses remain unchanged and can be reparsed.

Both become eligible only after scheduled game start plus an assumed 24 hours.
These are reconstructed availability times; original publication times remain
unknown. A current game's actual box score never supplies its pregame rotation.

## Rotation estimate

At one hour before scheduled tipoff:

1. Read only eligible earlier records from the same season.
2. Assign each player to the team in their latest eligible box-score observation.
   Ignore observations more than 30 days old.
3. Average the player's regulation-equivalent minutes over the team's last five
   eligible games, using zero when they did not appear. For each past team/game,
   normalize minutes to 240 before averaging to handle overtime and rounding.
4. Scale positive weights to total 240 minutes, with a maximum of 48 per player.
   If fewer than five players have positive weights, mark the rotation unavailable.
5. Require at least five eligible team games on both sides for a matchup to be
   marked eligible; retain warmup snapshots for inspection.

Each projected player also receives per-36-minute statistical rates from their
last ten eligible played games. Those rate histories can cross teams within the
same season. Record all source game IDs for minute projections and statistical
rates so a representation can be audited at its cutoff.

This recognizes a move only after a new-team observation becomes available. It
cannot know a trade before that first appearance, identify an unreported injury,
or distinguish every absence from a coach's decision. A player who leaves without
another observed team may remain a candidate until their weight/staleness rule
removes them. New players have no representation until observed. These are known
limitations, not an injury-aware roster feed. Do not claim actual trade-time or
availability accuracy from this proxy.

## Verification and next stage

Synthetic tests cover DNP separation, summary/result mismatches, cached collection,
warmup eligibility, capped minute totals, current/future stats and roster isolation,
observed team changes, missing observations, duplicate logs, and partial coverage.

After inspecting the bounded batch, expand player collection over the full training
history. Historical roster/transaction and injury snapshots remain separate work.
The next model can combine these statistical inputs with small player-identity
embeddings, then add the player synergy layer. Fit identity vocabularies and
normalizers on training data only; keep an unknown-player embedding. Compare
against Elo on matching eligible games and use separately reserved later periods
for any final accuracy claim.

## First batch results

Collected 120 games per season from 2022–23 and 2023–24: 240 games and 5,262
played-player records. Four listed rows had unknown participation because minutes
were unrecorded; they are retained only as observations. The original collection
process rejected two games before this edge-case fix. Rebuilding entirely from
checksum-verified raw caches with parser version 2 validated all 240 without any
new requests or rejected games.

The rotation builder produced 240 matchup snapshots; 78 met the five-prior-games
requirement for both teams. All nonempty projected team rotations were checked
for 240-minute totals and 48-minute individual caps. Warmup and insufficient-history
snapshots remain explicit. This validates the pipeline; the batch does not cover
the 611 benchmark test games or support a new accuracy claim yet.
