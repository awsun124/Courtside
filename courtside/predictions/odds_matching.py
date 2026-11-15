"""Join saved moneylines to upcoming ESPN games without requesting more odds.

Run from the project root: python -m courtside.predictions.odds_matching
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .history import BASE_URL, EASTERN, fetch_bytes, identifier, timestamp
from .odds import MIN_INTERVAL, PROJECT_ROOT, atomic_json, read_latest

# Provider labels are deliberately explicit; unknown names never fuzzy-match.
TEAM_IDS = {
    'Atlanta Hawks': '1', 'Boston Celtics': '2', 'Brooklyn Nets': '17',
    'Charlotte Hornets': '30', 'Chicago Bulls': '4', 'Cleveland Cavaliers': '5',
    'Dallas Mavericks': '6', 'Denver Nuggets': '7', 'Detroit Pistons': '8',
    'Golden State Warriors': '9', 'Houston Rockets': '10', 'Indiana Pacers': '11',
    'Los Angeles Clippers': '12', 'LA Clippers': '12', 'Los Angeles Lakers': '13',
    'Memphis Grizzlies': '29', 'Miami Heat': '14', 'Milwaukee Bucks': '15',
    'Minnesota Timberwolves': '16', 'New Orleans Pelicans': '3',
    'New York Knicks': '18', 'Oklahoma City Thunder': '25', 'Orlando Magic': '19',
    'Philadelphia 76ers': '20', 'Phoenix Suns': '21', 'Portland Trail Blazers': '22',
    'Sacramento Kings': '23', 'San Antonio Spurs': '24', 'Toronto Raptors': '28',
    'Utah Jazz': '26', 'Washington Wizards': '27',
}
START_TOLERANCE = timedelta(minutes=15)


def parse_schedule(payload):
    """Normalize scheduled events only; retain invalid records as diagnostics."""
    games, rejected = [], []
    if not isinstance(payload.get('events'), list):
        raise ValueError('ESPN scoreboard is missing events')
    for event in payload['events']:
        try:
            game_id = identifier(event['id'])
            if len(event['competitions']) != 1:
                raise ValueError('Expected one competition')
            competition = event['competitions'][0]
            status = competition['status']['type']
            if status['state'] != 'pre' or status.get('name') != 'STATUS_SCHEDULED':
                continue
            sides = {}
            for competitor in competition['competitors']:
                side = competitor['homeAway']
                if side not in {'home', 'away'} or side in sides:
                    raise ValueError('Invalid sides')
                sides[side] = identifier(competitor['team']['id'])
            if set(sides) != {'home', 'away'} or sides['home'] == sides['away']:
                raise ValueError('Expected distinct teams')
            games.append(dict(game_id=game_id, scheduled_start=timestamp(competition['date']).isoformat(),
                              home_team_id=sides['home'], away_team_id=sides['away']))
        except (KeyError, ValueError, TypeError, AttributeError):
            rejected.append(dict(reason='Invalid ESPN schedule event'))
    return games, rejected


def match_odds(snapshot, schedule, now):
    """Require a unique one-to-one match, correct sides and close start times."""
    retrieved = timestamp(snapshot['retrieved_at'])
    if retrieved > now:
        raise ValueError('Odds snapshot was retrieved in the future')
    schedule_counts = Counter(game['game_id'] for game in schedule)
    odds_counts = Counter(game['game_id'] for game in snapshot['games'])
    pending, unmatched = [], []
    for game in snapshot['games']:
        reason = None
        home, away = TEAM_IDS.get(game['home_team']), TEAM_IDS.get(game['away_team'])
        start = timestamp(game['commence_time'])
        candidates = []
        if odds_counts[game['game_id']] != 1:
            reason = 'Duplicate odds event ID'
        elif not home or not away:
            reason = 'Unknown provider team name'
        elif start <= now:
            reason = 'Odds event already started'
        else:
            candidates = [row for row in schedule
                          if row['home_team_id'] == identifier(home)
                          and row['away_team_id'] == identifier(away)
                          and timestamp(row['scheduled_start']) > now
                          and abs(timestamp(row['scheduled_start']) - start) <= START_TOLERANCE]
            if len(candidates) != 1:
                reason = 'Ambiguous ESPN matchup' if candidates else 'No matching scheduled ESPN game'
            elif schedule_counts[candidates[0]['game_id']] != 1:
                reason = 'Duplicate ESPN game ID'
        if reason:
            unmatched.append(dict(odds_game_id=game['game_id'], reason=reason))
        else:
            pending.append((game, candidates[0]))
    targets = Counter(row['game_id'] for _, row in pending)
    matched = []
    for game, target in pending:
        if targets[target['game_id']] != 1:
            unmatched.append(dict(odds_game_id=game['game_id'], reason='Multiple odds events match ESPN game'))
            continue
        books = []
        for book in game['bookmakers']:
            updated = timestamp(book['available_at'])
            stale = now - updated > MIN_INTERVAL or now - retrieved > MIN_INTERVAL
            books.append(dict(book, stale=stale, benchmark_eligible=not stale and updated <= retrieved))
        matched.append(dict(target, odds_game_id=game['game_id'],
                            odds_commence_time=game['commence_time'],
                            start_difference_seconds=abs((timestamp(target['scheduled_start']) -
                                                          timestamp(game['commence_time'])).total_seconds()),
                            bookmakers=books))
    return dict(matched=matched, unmatched=unmatched,
                schedule_without_odds=[row['game_id'] for row in schedule
                                       if row['game_id'] not in {g['game_id'] for g in matched}])


def build_report(folder, now, transport=fetch_bytes):
    pointer = Path(folder) / 'latest.json'
    metadata = json.loads(pointer.read_text()) if pointer.exists() else None
    snapshot = read_latest(folder)
    if snapshot is None:
        raise ValueError('No saved odds snapshot; run the odds collector first')
    if json.loads(pointer.read_text()) != metadata:
        raise ValueError('Odds snapshot changed during matching; run again')
    # Fresh schedule requests avoid reusing historical caches after a reschedule.
    days = sorted({timestamp(game['commence_time']).astimezone(EASTERN).date()
                   for game in snapshot['games'] if timestamp(game['commence_time']) > now})
    schedule, rejected, sources = [], [], []
    for day in days:
        url = BASE_URL + 'scoreboard?dates=' + day.strftime('%Y%m%d')
        payload = json.loads(transport(url))
        games, invalid = parse_schedule(payload)
        schedule.extend(games)
        rejected.extend(invalid)
        sources.append(dict(url=url, retrieved_at=datetime.now(timezone.utc).isoformat(), raw=payload))
    return dict(generated_at=now.isoformat(), odds_retrieved_at=snapshot['retrieved_at'],
                odds_snapshot=metadata,
                start_tolerance_seconds=START_TOLERANCE.total_seconds(),
                **match_odds(snapshot, schedule, now), rejected_schedule_records=rejected,
                schedule_sources=sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odds-folder', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/odds')
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'artifacts/predictions/odds-matches')
    args = parser.parse_args()
    try:
        now = datetime.now(timezone.utc)
        report = build_report(args.odds_folder, now)
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / (now.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        if path.exists():
            raise ValueError('Match report already exists')
        atomic_json(path, report)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'Odds matching failed: {error}\n')
    eligible = sum(book['benchmark_eligible'] for game in report['matched'] for book in game['bookmakers'])
    print(f"Matched {len(report['matched'])} games; unmatched odds: {len(report['unmatched'])}; "
          f"eligible bookmaker quotes: {eligible}.\nSaved {path}")


if __name__ == '__main__':
    main()
