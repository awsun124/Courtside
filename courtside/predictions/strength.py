"""Cutoff-safe, opponent-adjusted team strength for offline and local inference.

Only delayed final scores update strength. Upcoming fixtures need no result fields.
No learned preprocessing, market prices, player identities or current box scores.
"""

from collections import defaultdict
from datetime import timedelta
import heapq
import math

import numpy as np

from .features import validate_games
from .history import EASTERN, timestamp
from .odds_matching import TEAM_IDS

VERSION = 'margin-strength-v1'
HALF_LIVES = (60, 120, 240)
RATES = (0.03, 0.06, 0.10)
TEAMS = sorted({'espn:' + value for value in TEAM_IDS.values()})


def build_strength_rows(games, upcoming=(), *, as_of=None, delay_hours=24, min_history=5):
    """Replay in time order; target fields are separate from the feature matrix.

    Result availability is an assumption (start + 24 hours), not a verified
    historical publication timestamp. as_of caps all result updates for inference.
    Ridge strengths minimize recency-weighted squared score-margin error with a
    fixed three-point venue adjustment and ten pseudo-games of shrinkage.
    """
    if delay_hours < 6 or min_history < 1:
        raise ValueError('Invalid availability policy')
    validate_games(games)
    index = {team: i for i, team in enumerate(TEAMS)}
    records = [dict(g, has_result=True) for g in games]
    records += [dict(g, has_result=False) for g in upcoming]
    ids = [g['game_id'] for g in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate result or upcoming fixture')
    records.sort(key=lambda g: (timestamp(g['scheduled_start']), g['game_id']))
    n = len(TEAMS)
    gram = np.zeros((len(HALF_LIVES), n, n))
    rhs = np.zeros((len(HALF_LIVES), n))
    margin_ratings = np.zeros((len(RATES), n))
    elo = np.full(n, 1500.)
    reset_elo = elo.copy()
    pending, season, last_update = [], None, None
    histories, schedules = defaultdict(list), {}
    rows = []
    for game in records:
        start = timestamp(game['scheduled_start'])
        cutoff = start - timedelta(hours=1)
        available_at = min(cutoff, as_of) if as_of else cutoff
        home, away = (game[s]['team_id'] for s in ('home', 'away'))
        if home == away or home not in index or away not in index:
            raise ValueError('Invalid NBA team identity')
        while pending and pending[0][0] <= available_at:
            release, _, old, old_elo, old_reset, old_margins = heapq.heappop(pending)
            elapsed = (release - last_update).total_seconds() / 86400 if last_update else 0
            decay = np.exp2(-elapsed / np.asarray(HALF_LIVES))
            gram *= decay[:, None, None]
            rhs *= decay[:, None]
            last_update = release
            h, a = (index[old[s]['team_id']] for s in ('home', 'away'))
            margin = old['home']['points'] - old['away']['points']
            advantage = 3.0 if old.get('neutral_site') is False else 0.0
            x = np.zeros(n)
            x[h], x[a] = 1, -1
            gram += np.outer(x, x)
            rhs += x * (np.clip(margin, -35, 35) - advantage)
            change = np.asarray(RATES) * np.clip(margin - old_margins, -30, 30)
            margin_ratings[:, h] += change
            margin_ratings[:, a] -= change
            for ratings, expected in ((elo, old_elo), (reset_elo, old_reset)):
                change_elo = 20 * (int(old['home_win']) - expected)
                ratings[h] += change_elo
                ratings[a] -= change_elo
            for side, other in (('home', 'away'), ('away', 'home')):
                histories[old[side]['team_id']].append(dict(
                    season=old['season'], game_id=old['game_id'], release=release,
                    margin=old[side]['points'] - old[other]['points'],
                    points=old[side]['points'], allowed=old[other]['points']))
        if game['season'] != season:
            if season is not None:
                elo = 1500 + .75 * (elo - 1500)
                reset_elo[:] = 1500
                margin_ratings *= .65
            season = game['season']
        h, a = index[home], index[away]
        venue = int(game.get('neutral_site') is False)
        unknown_venue = int(game.get('neutral_site') is None)
        elo_logit = math.log(10) * (elo[h] - elo[a] + 50 * venue) / 400
        reset_logit = math.log(10) * (reset_elo[h] - reset_elo[a] + 50 * venue) / 400
        margins = margin_ratings[:, h] - margin_ratings[:, a] + 3 * venue
        features = dict(home_court=venue, neutral_site_unknown=unknown_venue, elo_logit=elo_logit)
        features.update({f'margin_{rate}': float(margins[i]) for i, rate in enumerate(RATES)})
        elapsed = max(0, (available_at - last_update).total_seconds() / 86400) if last_update else 0
        decay = np.exp2(-elapsed / np.asarray(HALF_LIVES))
        for i, days in enumerate(HALF_LIVES):
            strength = np.linalg.solve(gram[i] * decay[i] + 10 * np.eye(n), rhs[i] * decay[i])
            features[f'srs_{days}'] = float(strength[h] - strength[a] + 3 * venue)
        counts, latest, rests = {}, {}, {}
        for side, team in (('home', home), ('away', away)):
            history = [r for r in histories[team] if r['season'] == season]
            counts[side] = len(history)
            latest[side] = history[-1]['release'].isoformat() if history else None
            previous_start = schedules.get(team)
            rest = (start.astimezone(EASTERN).date() - previous_start.astimezone(EASTERN).date()).days if previous_start else 8
            rests[side] = min(max(rest - 1, 0), 7)
            features[side + '_back_to_back'] = int(rest == 1)
        features['rest_difference'] = rests['home'] - rests['away']
        features['history_difference'] = counts['home'] - counts['away']
        for window in (5, 10, 30):
            for statistic in ('margin', 'points', 'allowed', 'win', 'volatility'):
                def aggregate(team):
                    sample = [r for r in histories[team] if r['season'] == season][-window:]
                    if not sample:
                        return 0.
                    if statistic == 'win':
                        return sum(r['margin'] > 0 for r in sample) / len(sample)
                    if statistic == 'volatility':
                        return float(np.std([r['margin'] for r in sample]))
                    return sum(r[statistic] for r in sample) / len(sample)
                features[f'{statistic}_{window}_difference'] = aggregate(home) - aggregate(away)
        row = dict(game_id=game['game_id'], season=season, scheduled_start=start.isoformat(),
            cutoff_at=cutoff.isoformat(), inputs_as_of=available_at.isoformat(), features=features,
            eligible=min(counts.values()) >= min_history, history_counts=counts, latest_results=latest,
            elo_probability=1 / (1 + math.exp(-elo_logit)),
            reset_elo_probability=1 / (1 + math.exp(-reset_logit)))
        if game['has_result']:
            row.update(target=int(game['home_win']), margin=game['home']['points'] - game['away']['points'])
            heapq.heappush(pending, (start + timedelta(hours=delay_hours), game['game_id'], game,
                row['elo_probability'], row['reset_elo_probability'], margins.copy()))
        rows.append(row)
        schedules[home] = schedules[away] = start
    return rows
