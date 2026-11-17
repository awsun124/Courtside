# Court Side

An automated NBA briefing, stats dashboard and independent game prediction system.
Groups news into distinct stories, ranks the coverage, and records pregame win
probabilities alongside bookmaker odds and final results.

**[Live demo](https://courtside-agent.vercel.app)** · [Setup](docs/setup.md) · [Model & evaluation](docs/predictions.md)

## What it does

- Collects news and statistics daily at 6 AM Eastern. Related coverage becomes one story with original source links.
- Predicts game winners using team strength, opponent-adjusted scoring margins, venue, rest and confirmed player absences.
- Refreshes predictions and injuries at 6 AM and hourly from noon through 11 PM Eastern. Bookmaker odds are comparisons, not model inputs.
- Preserves forecast revisions in PostgreSQL and grades the latest valid prediction recorded at least one hour before tipoff.
- Separates live results from historical research. Browse prior dates under **Predictions → Past results**.
- Opens an assigned GitHub issue on collection failure and closes it when the workflow recovers. Repeat failures update the same issue.

## Evaluation

The original model was selected through chronological validation, frozen, then
evaluated on **501 games from February 1–April 12, 2026**. It correctly picked
**375 winners (74.9%)**, with a **0.1795 Brier score**. Elo picked 374 winners
(74.7%), with a 0.1827 Brier score on the same games.

An injury adjustment selected using earlier January games retained **375/501**
correct picks and improved Brier to **0.1786** in a retrospective comparison.
This reused the historical test period; it is not a new untouched evaluation.
The live record uses actual recorded forecasts and excludes these research games.
See [evaluation and production details](docs/predictions.md).

## Architecture

```mermaid
flowchart LR
  N[News feeds] --> C[Daily collection]
  S[NBA / ESPN stats] --> C
  C --> DB[(PostgreSQL)]
  G[Schedules and results] --> P[Prediction worker]
  I[Official injury reports] --> P
  O[Bookmaker odds] --> P
  M[Frozen independent model] --> P
  P --> DB
  DB --> A[FastAPI]
  A --> UI[React / TypeScript]
  C --> H[GitHub failure alerts]
  P --> H
```

GitHub Actions runs ingestion. FastAPI serves saved records; page loads and
filters do not spend provider API credits. React/Vite is hosted on Vercel, the
API on Render, and PostgreSQL on Neon. SQLite supports local development.
No LLM calls run in the scheduled production jobs.

## Run locally

Follow [setup](docs/setup.md) for environment variables, database migration and
backend startup. Sample news works without provider keys. Never place credentials
in browser variables or commit `.env`.

```sh
npm --prefix frontend install
npm --prefix frontend run dev
```

## Checks

```sh
.venv/bin/python -m unittest discover -s courtside -t .
npm --prefix frontend test
npm --prefix frontend run build
```

[Architecture](docs/architecture.md) · [Ranking](docs/ranking.md) · [Deployment](docs/deployment.md)
