"""Store saved predictions, final results and collection status in SQLite or PostgreSQL."""

from contextlib import closing
from datetime import date,timedelta
import hashlib
import json
import math

from ..database import connect
from .history import timestamp,EASTERN
from .service import public_game

SCHEMA='''
CREATE TABLE IF NOT EXISTS prediction_observations (
 observation_id TEXT PRIMARY KEY,
 game_id TEXT NOT NULL,
 game_date TEXT NOT NULL,
 saved_at TEXT NOT NULL,
 scheduled_start TEXT NOT NULL,
 cutoff_at TEXT NOT NULL,
 model_version TEXT,
 payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prediction_observations_date ON prediction_observations(game_date, game_id, saved_at);
CREATE TABLE IF NOT EXISTS prediction_outcomes (
 game_id TEXT PRIMARY KEY,
 game_date TEXT NOT NULL,
 scheduled_start TEXT NOT NULL,
 recorded_at TEXT NOT NULL,
 payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prediction_collection_state (
 state_key TEXT PRIMARY KEY,
 updated_at TEXT NOT NULL,
 payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prediction_job_leases (
 job_key TEXT PRIMARY KEY,
 lease_until TEXT NOT NULL
);
'''


def migrate(database):
    with closing(connect(database)) as c,c:
        for sql in SCHEMA.split(';'):
            if sql.strip():c.execute(sql)


def save_snapshot(database,report,now):
    if report['schema_version']!=1 or report['timezone']!='America/New_York':raise ValueError('Invalid prediction edition')
    saved=timestamp(report['saved_at'])
    if saved>now:raise ValueError('Future prediction edition')
    seen=set()
    with closing(connect(database)) as c,c:
        for game in report['games']:
            if game['game_id'] in seen:raise ValueError('Duplicate prediction')
            seen.add(game['game_id'])
            public_game(game,report['saved_at'],now)
            if timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat()!=report['date']:
                raise ValueError('Prediction date mismatch')
            raw=json.dumps(game,sort_keys=True,allow_nan=False)
            identity=hashlib.sha256((report['saved_at']+raw).encode()).hexdigest()
            c.execute('INSERT INTO prediction_observations (observation_id,game_id,game_date,saved_at,scheduled_start,cutoff_at,model_version,payload) '
                'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(observation_id) DO NOTHING',
                (identity,game['game_id'],report['date'],saved.isoformat(),timestamp(game['scheduled_start']).isoformat(),
                 timestamp(game['cutoff_at']).isoformat(),game.get('model_version') if game['model_status']=='available' else None,raw))


def save_outcomes(database,games,now):
    from .features import validate_games
    validate_games(games)
    with closing(connect(database)) as c,c:
        for game in games:
            if timestamp(game['scheduled_start'])>=now:raise ValueError('Future final result')
            # Final scores may be corrected upstream; immutable forecasts are untouched.
            c.execute('INSERT INTO prediction_outcomes (game_id,game_date,scheduled_start,recorded_at,payload) VALUES (?,?,?,?,?) '
                'ON CONFLICT(game_id) DO UPDATE SET recorded_at=excluded.recorded_at,payload=excluded.payload',
                (game['game_id'],timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat(),
                 game['scheduled_start'],now.isoformat(),json.dumps(game,allow_nan=False)))


def load_outcomes(database):
    with closing(connect(database)) as c:
        return [json.loads(r[0]) for r in c.execute('SELECT payload FROM prediction_outcomes ORDER BY scheduled_start, game_id').fetchall()]


def put_state(database,key,payload,now):
    with closing(connect(database)) as c,c:
        c.execute('INSERT INTO prediction_collection_state (state_key,updated_at,payload) VALUES (?,?,?) '
            'ON CONFLICT(state_key) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload',
            (key,now.isoformat(),json.dumps(payload,allow_nan=False)))


def get_state(database,key):
    with closing(connect(database)) as c:
        r=c.execute('SELECT payload FROM prediction_collection_state WHERE state_key=?',(key,)).fetchone()
    return json.loads(r[0]) if r else None


def claim_job(database,now):
    until=(now+timedelta(minutes=20)).isoformat()
    with closing(connect(database)) as c,c:
        row=c.execute('INSERT INTO prediction_job_leases (job_key,lease_until) VALUES (?,?) '
            'ON CONFLICT(job_key) DO UPDATE SET lease_until=excluded.lease_until '
            'WHERE prediction_job_leases.lease_until<=? RETURNING job_key',('daily-predictions',until,now.isoformat())).fetchone()
    return row is not None


def release_job(database):
    with closing(connect(database)) as c,c:
        c.execute('DELETE FROM prediction_job_leases WHERE job_key=?',('daily-predictions',))


def select_observations(records,now):
    """Latest on-time forecast is frozen once the one-hour cutoff passes.

    Preserve every observation. Late/missing updates never replace an eligible
    forecast. Display and grading choose the same snapshot, independently of result.
    """
    chosen={}
    for saved_at,raw in records:
        game=json.loads(raw);saved=timestamp(saved_at)
        if saved>now:continue
        eligible=game['model_status']=='available' and saved<=timestamp(game['cutoff_at'])
        key=game['game_id'];old=chosen.get(key)
        if old is None or (eligible and (not old[2] or saved>old[0])) or (not eligible and not old[2] and saved>old[0]):
            chosen[key]=(saved,game,eligible)
    return chosen


def latest_query(condition):
    """Select one saved prediction per game in SQL instead of loading every version."""
    return ('SELECT saved_at,payload FROM (SELECT saved_at,payload, '
        'ROW_NUMBER() OVER (PARTITION BY game_id ORDER BY '
        'CASE WHEN model_version IS NOT NULL AND saved_at<=cutoff_at THEN 1 ELSE 0 END DESC, '
        'saved_at DESC,observation_id DESC) AS position FROM prediction_observations WHERE '+condition+
        ') observations WHERE position=1')


def read_day(database,day,now):
    day=date.fromisoformat(day).isoformat()
    with closing(connect(database)) as c:
        records=c.execute(latest_query('game_date=? AND saved_at<=?'),(day,now.isoformat())).fetchall()
        outcomes={r[0]:json.loads(r[1]) for r in c.execute('SELECT game_id,payload FROM prediction_outcomes WHERE game_date=?',(day,)).fetchall()}
    rows=[];selected=select_observations(records,now)
    for key,(saved,game,eligible) in selected.items():
        row=public_game(game,saved.isoformat(),now)
        result=outcomes.get(key)
        if eligible and result:
            if any(result[side]['team_id']!=game[side+'_team_id'] for side in ('home','away')):
                raise ValueError('Result team mismatch')
            row['result']=dict(home_points=result['home']['points'],away_points=result['away']['points'],
                correct=(row['home_probability']>=.5)==result['home_win'])
        rows.append(row)
    return dict(date=day,timezone='America/New_York',checked_at=now.isoformat(),
        saved_at=max((x[0] for x in selected.values()),default=None).isoformat() if selected else None,
        status='available' if selected else 'no_snapshot',games=sorted(rows,key=lambda r:(r['scheduled_start'],r['game_id'])))


def performance(database,now):
    # Bound the live evaluation to the most recent year. Historical research is
    # a separate asset and never imported as prospective performance.
    first=(now.astimezone(EASTERN).date()-timedelta(days=365)).isoformat()
    with closing(connect(database)) as c:
        records=c.execute(latest_query('game_date>=? AND saved_at<=?'),(first,now.isoformat())).fetchall()
        results={r[0]:json.loads(r[1]) for r in c.execute('SELECT game_id,payload FROM prediction_outcomes WHERE game_date>=?',(first,)).fetchall()}
    selected=select_observations(records,now);scored=[];pending=0
    for key,(saved,game,eligible) in selected.items():
        if not eligible:continue
        result=results.get(key)
        if result is None:pending+=1;continue
        if any(result[side]['team_id']!=game[side+'_team_id'] for side in ('home','away')):raise ValueError('Result team mismatch')
        p=game['home_probability'];y=int(result['home_win'])
        scored.append(dict(date=timestamp(game['scheduled_start']).astimezone(EASTERN).date().isoformat(),
            correct=(p>=.5)==bool(y),brier=(p-y)**2,log_loss=-(y*math.log(p)+(1-y)*math.log(1-p))))
    count=len(scored);correct=sum(r['correct'] for r in scored)
    monthly=[]
    for month in sorted({r['date'][:7] for r in scored}):
        group=[r for r in scored if r['date'].startswith(month)]
        monthly.append(dict(month=month,games=len(group),correct=sum(r['correct'] for r in group),
                            accuracy=sum(r['correct'] for r in group)/len(group)))
    return dict(games=count,correct=correct,accuracy=correct/count if count else None,
        brier=sum(r['brier'] for r in scored)/count if count else None,pending_games=pending,
        first_date=min((r['date'] for r in scored),default=None),last_date=max((r['date'] for r in scored),default=None),
        months=monthly,policy='Latest prediction recorded at least one hour before tipoff. Historical research games excluded.')
