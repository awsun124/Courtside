# Free-tier moneyline collector

The server-side collector reads `ODDS_API_KEY` from the environment or the root
`.env` file. The environment takes precedence. Parsing `.env` never executes shell
code. `.env` remains ignored by Git; `.env.example` contains only the variable name.

```env
ODDS_API_KEY=your_actual_key_here
```

```bash
.venv/bin/python scripts/collect_prediction_odds.py
```

Optional `--env-file` and `--output` paths support local setup. No key is accepted
as a command-line argument, put in a request URL, passed to frontend code, or
printed. The documented `x-api-key` header is supplied to curl through stdin.
Provider error bodies/stderr are not echoed or persisted. Successful responses
containing the credential are rejected rather than saved.

## Request and quota policy

One NBA request asks for US-region `h2h` moneylines in decimal format. The provider
documents one credit per region per market for this endpoint, so this request
normally costs one credit, regardless of the number of games/bookmakers returned.
See [API authentication and quota documentation](https://the-odds-api.com/liveapi/guides/v4/).

- Reuse the latest snapshot for six hours; no provider request or key loading is
  needed on a cache hit.
- Cap local attempts at 124 per UTC calendar month. Failed attempts count
  conservatively. Actual provider credit usage is recorded separately.
- Record `x-requests-remaining`, `x-requests-used`, and `x-requests-last` when returned.
  Stop if the last remaining-credit count is zero. The local month ledger is not
  a replacement for the provider's billing/reset schedule or usage by other apps.
- Make no automatic retries. Requests have bounded time/size and HTTPS-only URLs.
- An advisory file lock prevents concurrent collectors from double spending.

The collector is an explicit CLI job. It is not yet scheduled, called from web
page loads, or attached to the existing news collection job. This avoids spending
credits as a side effect of opening the app.

## Snapshots and normalization

Ignored `artifacts/predictions/odds/` contains immutable timestamped snapshots,
an atomic `latest.json` pointer with a checksum, local request ledgers, and a lock
file. Snapshots retain request/retrieval times, the credential-free request URL,
quota metadata, raw successful events, normalized upcoming games, and rejection
counts. A failed refresh preserves earlier snapshots and the latest pointer.
Snapshots contain public odds data, never credentials.

NBA event IDs use the `odds:` namespace. They have not yet been matched to ESPN
IDs, so this is market data collection, not a completed model-versus-market join.
Game/team labels, future start times, bookmakers, two-sided moneylines, finite
decimal prices above one, and update timestamps are validated. Already-started
events are skipped. Invalid books/events are counted with generic reasons;
missing markets remain unavailable rather than receiving fabricated odds.

For each bookmaker, retain decimal prices and their break-even probabilities
`1 / price`. Normalize the two implied probabilities by their sum for a simple
margin-adjusted market estimate. These two quantities are different: the offered
price determines break-even; the normalized pair is a forecasting benchmark.
Bookmaker pairs are not mixed to compute a probability.

Prices older than six hours are flagged stale. Cache reads through the collector
recheck staleness and remove games that started since collection. Other callers
should pass their current UTC time to `read_latest(folder, now)` for the same
filtering; reading without a time returns the original historical snapshot.
Saved raw data and immutable snapshots do not change when filtered for display.

## Live validation

On November 15, 2026, one successful request returned eight upcoming NBA games
and 71 bookmaker moneylines, with zero rejected records. It consumed one credit
and reported 499 remaining. A repeated CLI run reused the cache without another
request. No historical odds were requested.

Tests cover literal key loading, environment precedence, stdin authentication,
credential/error redaction, probability normalization, invalid markets/timestamps,
started-game filtering, cached reuse, failed-refresh preservation, local/remote
quota guards, concurrent locks, and checksum tampering.

Next match upcoming ESPN games to provider events with explicit team mappings
and start-time checks, then connect saved quotes to the daily prediction job.
The Predictions API/page and automatic schedule are not implemented by this step.
