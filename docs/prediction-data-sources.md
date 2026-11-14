# Prediction data source audit

Researched November 14, 2026. This is a source-selection report, not a completed
dataset coverage audit. No paid accounts, subscriptions, or credentials were used.
The samples below establish access, not full-season completeness.

## Recommended starting stack

Use NBA IDs as canonical player/team/game identifiers. Start with cached public
historical data for research, verify it against schedules and box scores, and keep
provider adapters separate from feature construction. Build the team baseline
before waiting on historical injuries or buying odds.

| Need | First choice | Alternative | Main unresolved issue |
| --- | --- | --- | --- |
| Historical game/player logs | NBA Stats via `nba_api`, if reachable | Archived NBA play-by-play plus box-score validation; paid BALLDONTLIE | NBA Stats endpoint timed out locally; bulk reconstruction needs validation |
| Historical play-by-play | `shufinskiy/nba_data` bulk files | Fetch individual games through NBA feeds | Listed files do not establish complete season coverage |
| Possessions and on-court lineups | `pbpstats` processing cached game data | Own stint reconstruction only if needed | Substitution errors and incomplete lineups need checks |
| Schedule/results cross-check | ESPN public scoreboard | NBA game metadata | ESPN feed is undocumented and has different IDs |
| Pregame injury statuses | Official NBA timestamped reports | Paid availability API with verified historical snapshots | Historical discovery, name mapping, parsing, and completeness |
| Timestamped moneylines | The Odds API historical snapshots | Evaluate BALLDONTLIE opening odds for a separate opening-price study | Paid access; exact same-cutoff coverage must be measured |

## Sources and what was verified

### NBA Stats and nba_api

`nba_api` is a community client for NBA endpoints, not an NBA-supported service
contract. It documents game logs and provides player/team reference IDs without
an API key. Prefer league-wide season requests over one request per player.
Its maintainers document throttling and cloud-IP access problems.

A bounded 12-second `leaguegamelog` request for 2025–26 returned no bytes and timed
out in this environment. That does not establish that every NBA endpoint is
unavailable. Do not solve failures with unbounded retries or make production page
loads depend on this host.

References: [client](https://github.com/swar/nba_api),
[endpoint guide](https://github.com/swar/nba_api/blob/master/docs/nba_api/stats/endpoint_guide.md).

### Bulk archive: shufinskiy/nba_data

The repository distributes historical NBA play-by-play datasets and collection
scripts. Its README headline stops at 2024–25, but the live download manifest has
newer entries. An HTTP 200 manifest read confirmed these NBA regular-season keys:

- 2024–25: `cdnnba_2024`, `nbastats_2024`, `nbastatsv3_2024`, `pbpstats_2024`, and others.
- 2025–26: `cdnnba_2025`, `nbastatsv3_2025`, `matchups_2025`, `shotdetail_2025`.
- No NBA `pbpstats_2025` entry was present in the checked manifest.

The year is the season's starting year. These are inventory observations only:
the archives were not downloaded, row-counted, or checked for completeness. Pin
the manifest revision and checksum downloaded files. Keep original provenance;
the repository's code license alone is not a guarantee about upstream data rights.

References: [repository](https://github.com/shufinskiy/nba_data),
[manifest](https://raw.githubusercontent.com/shufinskiy/nba_data/main/list_data.txt).

### pbpstats

This Python package parses NBA play-by-play into possessions and adds the lineup
on the floor for events. It also handles some event-order problems. It is useful
for training player/lineup models after the basic game-level pipeline works.
It is a parser, not a promise of complete hosted historical data. Validate raw
provider compatibility, lineup reconstruction, and missing games first.

Reference: [maintainer documentation](https://pbpstats.readthedocs.io/en/latest/).

### ESPN public scoreboard

The project already uses ESPN for standings. A direct historical scoreboard read
returned HTTP 200 and two events for October 21, 2025: Houston at Oklahoma City
and Golden State at Los Angeles Lakers, with event IDs and UTC start times.

Use this as a schedule/results cross-check, not the primary historical injury or
odds archive. A historical scoreboard can contain subsequently updated fields;
its availability today does not prove those fields were known before tipoff.
There is no verified service guarantee or full-season coverage from this sample.
Map events by teams and scheduled time, then save an explicit ID crosswalk.

Sample: [historical scoreboard](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20251021).

### Official NBA injury reports

The NBA publishes timestamped injury-report PDFs throughout the day. The current
season page exposes report links, but requesting the 2025–26 page redirected to
2026–27 during research. Search also located individual older PDF URLs; that is
not evidence of a complete discoverable archive.

Use report publication time and game date, preserve the raw PDF, and resolve names
to player IDs. Missing from a report must not automatically mean available.
Reports do not provide a complete roster or projected minutes. Before an
injury-aware historical study, measure report coverage at the chosen cutoff and
audit parsed rows manually. A lagged-minutes baseline can proceed independently.

References: [NBA report page](https://official.nba.com/nba-injury-report-2026-27-season/),
[historical PDF example](https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-04-26_01PM.pdf).

### The Odds API

Historical featured-market odds start in June 2020; snapshots are every five
minutes from September 2022. Historical data requires a paid usage plan. The API
returns the nearest snapshot at or before the requested time. No authenticated
data was fetched and no price quotation or purchase was made.

For the market comparison, request NBA `h2h` moneylines, preserve both outcomes,
bookmaker and source update time, and reject odds recorded after the cutoff or
from an in-progress game. Discover bookmaker coverage before bulk extraction.
Missing/stale prices should be counted, not silently filled from closing odds.

Reference: [provider historical documentation](https://the-odds-api.com/historical-odds-data/).

### BALLDONTLIE

The documented free NBA tier includes teams, players, and games. Player game
stats and injuries require a paid tier. The docs describe odds from the 2025
season onward and opening-odds endpoints. Historical games are not automatically
historical injury snapshots, and current odds are not an arbitrary-time archive.

Potential paid convenience option if direct NBA access remains unreliable. Before
choosing it, confirm season coverage, rate limits, which plan includes each needed
endpoint, and whether prices preserve the intended prediction cutoff. Opening
odds support an opening-price comparison, not necessarily a one-hour-pregame one.
No keyed endpoint was tested. Provider IDs need a crosswalk to canonical NBA IDs.

Reference: [provider documentation and tier matrix](https://docs.balldontlie.io/).

## Collector design

Follow existing small Python modules and explicit parsing functions. Do not add
an LLM/news agent to collect numeric sports records.

- Fetch/cache raw responses under ignored `artifacts/predictions/raw/` before parsing.
- Record source URL without credentials, retrieval time, provider timestamp,
  season, checksum, and parser version in a sidecar manifest.
- Keep credentials in environment variables. Bound request time/size, retry only
  transient failures, respect documented limits, and support resuming by season.
- Separate fetch, parse, validate, and persist functions. Do not switch providers
  silently: record which provider produced each record and validate ID mappings.
- Check duplicate game/player keys, canceled/postponed games, regular-season
  classification, result consistency, box-score totals, and timezones. Preserve
  IDs as strings, including leading zeros. Exclude Cup final/exhibition games
  when they do not count toward regular-season records.
- Distinguish retrieval time from historical knowledge time. Backfilled box
  scores do not supply their original publication timestamps. Document a
  conservative availability assumption and evaluate sensitivity to it.
- Produce a season coverage report before features: games expected/observed,
  player logs, missing IDs, usable roster/injury snapshots, odds at cutoff, and
  the final common eligible sample.

## Next implementation milestone

1. Read and pin the bulk manifest; inspect one small game/file before any large download.
2. Obtain historical team/game records and player box scores. Validate a small
   multi-game sample, then inventory seasons through 2025–26.
3. Build lagged team-strength/rest features and a chronological baseline without odds.
4. Add timestamped odds after access is available; measure common-game coverage.
5. Resolve historical rosters/availability before claiming injury-aware performance.

No fully verified free source for all 2025–26 pregame moneylines at a chosen
timestamp was established. This limits the market benchmark, not the ability to
build and evaluate the initial forecasting baseline against game outcomes.
