# Predictions

## Model

The model combines Elo, opponent-adjusted scoring margins, venue and rest.
Confirmed player absences add an adjustment chosen using earlier games.
In a historical comparison covering
February 1–April 12, 2026, it picked 375/501 winners (74.9%), with a
0.17856 Brier score. The original model scored 0.17951. The injury weight (0.25)
was chosen on January validation. [Research details](../courtside/predictions/IMPROVED_MODEL.md).

The production model uses data-only linear coefficients and 4,920 historical
regular-season results under `courtside/predictions/assets/`. It reproduces the
original checkpoint to numerical precision and needs no local pickle files.
Only the scheduled worker installs `requirements-predictions.txt`. The API reads
stored observations and requires no ML dependencies.

## Production workflow

`NBA predictions` runs in GitHub Actions at 6 AM Eastern and hourly from noon
through 11 PM Eastern. It also supports manual dispatch. GitHub can delay cron
runs. Configure `DATABASE_URL` and optional `ODDS_API_KEY` as repository secrets.
The job:

1. Collects final ESPN results and settles previously recorded predictions.
2. Refreshes upcoming schedules and generates independent model forecasts.
3. Checks official NBA injury reports and recent player box scores.
4. Attaches bookmaker prices when available, with a six-hour odds cache and a
   durable 124-attempt monthly cap. Page loads never spend odds credits.
5. Saves immutable observations in PostgreSQL. The latest valid forecast before
   the one-hour cutoff becomes the displayed and graded prediction.

Results, forecast revisions, result-coverage state and odds usage survive worker
and API restarts. A database lease plus workflow concurrency prevents overlapping
collection. Provider caches in GitHub Actions reduce repeated public requests.
Injury reports marked not yet submitted or missing player data remain explicit.
Questionable players are not assumed out. Existing long-term absences receive a
smaller or zero additional penalty. Confirmed starting lineups are not included.

Apply the additive schema using the existing direct-URL migration command before
the first worker run. Test migrations on a Neon branch first. The worker uses the
pooled database connection and does not perform PostgreSQL schema changes.

```sh
python -m scripts.migrate_database --env-file .env
pip install -r requirements-predictions.txt
python -m courtside.predictions.production
```

## API and page

`GET /api/predictions?date=YYYY-MM-DD` reads PostgreSQL by default (SQLite locally).
The date is Eastern. The response includes recorded pregame forecasts, official
injury notes, optional bookmaker prices, final scores and performance totals.
Missing market prices hide comparison columns. Key absences appear on matchup cards;
the full player list can be expanded.
The page shows the previous seven days of results. Overall and monthly accuracy
remain available in the API’s `performance` field but are not displayed on the page.

Backend performance totals include only recorded forecasts with settled game results
from the last year, using one forecast per game. Research evaluation games are
not imported into that record. Pending games do not count in its accuracy.
Monthly totals are calculated once results exist. Final score corrections can update
settlement without changing the saved prediction.

For legacy file-based previews, explicitly set `COURTSIDE_PREDICTIONS_DIR` or pass
`predictions_folder` to `create_app`. This mode keeps the existing first-forecast
file-reader behavior and does not include database live-performance data.
Production should leave that override unset.

## Tests

```sh
.venv/bin/python -m unittest discover -s courtside/predictions -t .
npm --prefix frontend test
npm --prefix frontend run build
```

Tests cover immutable revisions, on-time selection, late-update rejection,
settlement, repeat-run idempotency, concurrent-job leases, missing market data,
player-availability disclosures and live-versus-historical result separation.
Secrets remain in the server environment or encrypted Actions secrets. Never put
provider or database credentials in browser variables or committed assets.
