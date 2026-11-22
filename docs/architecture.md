# Architecture

The [prediction guide](predictions.md) covers the independent model, evaluation
and saved forecast API.

FastAPI collects articles, groups events, calculates scores, and saves briefings
in SQLite locally or PostgreSQL when DATABASE_URL is set. React reads the saved results. Opening pages and filtering stories do
not call news or stats providers.

- courtside/sources.py combines GNews, enabled RSS feeds, and FreeNewsApi.ai.
- courtside/stories.py filters articles and groups reports about the same event.
- courtside/ranking.py calculates scores. The investigator cannot change them.
- courtside/investigation.py checks story excerpts within fixed time and tool-call limits.
- courtside/storage.py and schema.sql store articles, groups, briefings, and logs.
- courtside/schedule.py runs the shared daily collection.
- courtside/stats.py caches NBA.com player stats and ESPN team records.

The investigator can search saved articles, retrieve allowed excerpts, inspect
related groups, and compare previous briefings. Limits live in
config/investigation.json. Tool calls, citations, and short decisions are saved.
Article text is treated as source material, never as instructions. Sources
restricted to headline attribution are excluded from investigation input. Summaries must quote retrieved evidence. The rules-based investigator checks for
conflicting player-availability reports and explicit corrections. Suspicious
instructions are quarantined. These checks cannot establish that a source is true.

## Evaluation and operations

GitHub Actions runs news and stats collection daily, and refreshes predictions,
injuries and results on their own schedule. Both jobs save to PostgreSQL and open
or update a GitHub issue on failure. FastAPI serves those saved records; the
frontend does not call the providers directly. Bookmaker odds are stored for
comparison and never used as inputs to the independent prediction model.

The news quality workflow runs investigator tests and 33 development cases on
pull requests and pushes to main. It uploads reports for 14 days. The quality
check must be configured as required in branch rules to block merges.

`courtside/evals/production.py` reads saved live records on demand. It checks
citations and tool limits, writes private local reports and records reviewer
verdicts separately. Reviewed failures can become development regression cases.
It does not fetch news or run on a schedule. Reports stay in the ignored artifacts
folder. New investigations record code hashes and elapsed time; old records may
lack that provenance.

## Data limits

GDELT validation returned rate limits and timeouts, so the app uses other
sources. Free GNews access is
12 hours delayed. RSS feeds show recent articles and may not cover the full seven-day window.
FreeNewsApi.ai contributes attributed headlines without storing article bodies.

Publisher groups and event matches are estimates. Copied reports, ambiguous names,
and short excerpts can cause mistakes. Related reading adds context without
contributing to scores or corroboration. Rumors stay labeled. Freshness remains
zero until a specific new development has been reviewed and linked to evidence.

Player stats come from NBA.com and team records from ESPN. Stats are per game, regular season, and cached daily.
Minimum games is a display rule, not official NBA qualification. Team ties are
alphabetical rather than official playoff seeding. Failed sources keep old data
with their original timestamp and a visible warning.

Team filters, keyword search, bookmarks, unread markers, and spoiler preferences
run in the browser. Bookmarks keep up to 50 saved stories. Read status keeps up
to 2,000 story IDs. These preferences do not sync between devices. New event IDs
can make an old story appear unread again.
