# Saved Predictions API and page

`GET /api/predictions?date=2026-11-15` reads saved daily snapshots. Omit `date`
to use today's America/New_York date. Invalid dates return 422, missing snapshots
return 200 with `status: "no_snapshot"` and an empty `games` array, and unreadable
snapshots return a generic 503. There is no write endpoint. Requests never load
provider credentials, collect data, generate predictions or spend odds credits.

The default input is `artifacts/predictions/daily/`. Set the backend environment
variable `COURTSIDE_PREDICTIONS_DIR` to override that directory. `create_app` also
accepts `predictions_folder` for tests or embedding. Responses contain only the
public matchup/forecast/bookmaker fields, not source payloads or internal paths.

The API combines a date's immutable snapshots. It keeps each game's first
available model forecast with the quotes recorded alongside it. Later runs
cannot erase started games or replace an already available forecast. An earlier
unavailable state can be replaced by a later valid forecast. `saved_at` on each
game identifies its selected observation; the response-level `saved_at` is the
latest edition read. Read requests do not modify the saved files.

Quote freshness and tipoff-passed flags are computed at request time. A saved
model-minus-market difference remains a historical percentage-point comparison
even after its quote becomes stale. `benchmark_eligible_at_save` describes the
original cutoff eligibility; `current` also requires a fresh quote and a future
tipoff. Neither field is a recommendation to wager.

The frontend `/predictions` route includes an Eastern date selector, saved model
probabilities, separate bookmaker prices/probabilities, percentage-point
differences and forecast timestamps. Bookmaker switching uses the loaded
response. Reloading requests saved results only. Missing models/markets, stale
quotes, historical observations, empty editions and API errors are explicit.
Vercel rewrites `/predictions` to the existing application entry point.

## Deployment

Snapshot/model artifacts are ignored by Git. Deploying source alone does not
transfer local forecasts to Render or another backend. Run the prediction CLI
on the backend's persistent storage, or copy approved daily snapshot files there,
then point `COURTSIDE_PREDICTIONS_DIR` at that directory. Until files exist, the
page correctly displays no saved predictions. No collection schedule or transfer
is installed by the API/page implementation. The frontend keeps using the
existing `VITE_API_BASE_URL` setting.

Verification includes endpoint date validation/read-only behavior, source-field
projection, preservation across daily runs, stale historical quotes, malformed
snapshots, frontend date selection, bookmaker switching without requests,
missing-data states, error retry and the existing application test suite.
