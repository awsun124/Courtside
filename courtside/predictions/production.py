"""Scheduled prediction ingestion, injury refresh, market cache and settlement.

Run with DATABASE_URL (or COURTSIDE_DATABASE locally). Page requests never run it.
The database schema must be migrated separately before production collection.
"""

import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path

from threadpoolctl import threadpool_limits

from ..database import database_setting,is_postgres
from .availability import adjust_forecasts,collect_report,collect_players
from .daily import build_daily
from .forecast_strength import forecast_games
from .history import BASE_URL,EASTERN,fetch_bytes,parse_result,timestamp
from .injury_calibration import apply_weight,DEFAULT_WEIGHT
from .odds import collect_odds,read_latest,atomic_json
from .odds_matching import match_odds,parse_schedule
from .production_model import load_model
from .repository import (migrate,save_snapshot,save_outcomes,load_outcomes,get_state,put_state,claim_job,release_job)


def get_scoreboard(day):
    url=BASE_URL+'scoreboard?dates='+day.strftime('%Y%m%d')
    raw=fetch_bytes(url);payload=json.loads(raw)
    if not isinstance(payload.get('events'),list):raise ValueError('Missing scoreboard inventory')
    return payload,dict(url=url,retrieved_at=datetime.now(timezone.utc).isoformat(),
        sha256=hashlib.sha256(raw).hexdigest(),raw=payload)


def update_results(database,now):
    today=now.astimezone(EASTERN).date()
    year=today.year if today.month>=10 else today.year-1
    start=today.replace(year=year,month=10,day=1)
    state=get_state(database,'prediction-result-coverage')
    if state:
        start=max(start,datetime.fromisoformat(state['through']).date()-timedelta(days=2))
    results=[]
    for offset in range((today-start).days+1):
        day=start+timedelta(days=offset);payload,source=get_scoreboard(day)
        for event in payload['events']:
            if event['season']['type']!=2:continue
            competition=event['competitions'][0];status=competition['status']['type']
            notes=' '.join(n.get('headline','') for n in competition.get('notes',[])).lower()
            if (status.get('name') in ('STATUS_POSTPONED','STATUS_CANCELED') or
                competition.get('type',{}).get('abbreviation')=='ALLSTAR' or
                'championship' in notes and any(w in notes for w in ('cup','in-season tournament'))):continue
            if not status.get('completed'):
                if day==today:continue
                raise ValueError('Unresolved historical game; preserve prior forecasts')
            result=parse_result(dict(header=event),event['id'])
            if timestamp(result['scheduled_start']).astimezone(EASTERN).date()!=day:
                raise ValueError('Result date differs from requested date')
            result['source']={k:v for k,v in source.items() if k!='raw'}
            results.append(result)
    save_outcomes(database,results,datetime.now(timezone.utc))
    put_state(database,'prediction-result-coverage',dict(through=(today-timedelta(days=1)).isoformat()),now)
    return load_outcomes(database)


def restore_odds(database,folder):
    state=get_state(database,'prediction-odds-cache')
    if not state:return
    for relative,payload in state.items():
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts or path.suffix!='.json':raise ValueError('Unsafe saved odds cache path')
        (folder/path).parent.mkdir(parents=True,exist_ok=True)
        atomic_json(folder/path,payload)


def persist_odds(database,folder,now):
    paths=list(folder.glob('requests-*.json'))
    pointer=folder/'latest.json'
    if pointer.exists():
        latest=json.loads(pointer.read_text());paths+=[pointer,folder/'snapshots'/Path(latest['snapshot']).name]
    put_state(database,'prediction-odds-cache',{str(p.relative_to(folder)):json.loads(p.read_text()) for p in paths},now)


def run(database,cache,now=None):
    now=now or datetime.now(timezone.utc)
    if not is_postgres(database):migrate(database)
    if not claim_job(database,now):
        print('Prediction job already running; skipped.',flush=True);return
    try:
        metadata,model,old_games=load_model()
        current=update_results(database,now)
        known={g['game_id'] for g in old_games}
        history=old_games+[g for g in current if g['game_id'] not in known]
        today=now.astimezone(EASTERN).date();schedule=[];sources=[]
        for day in (today,today+timedelta(days=1)):
            payload,source=get_scoreboard(day);rows,rejected=parse_schedule(payload)
            if rejected:raise ValueError('Invalid upcoming schedule')
            schedule+=rows;sources.append(source)
        odds_folder=cache/'odds';odds_folder.mkdir(parents=True,exist_ok=True)
        restore_odds(database,odds_folder)
        market_error=None;snapshot=None
        try:
            snapshot,_=collect_odds(odds_folder)
        except ValueError as error:
            # Credentials are not present in the collector's error messages.
            market_error=str(error);snapshot=read_latest(odds_folder)
        finally:persist_odds(database,odds_folder,datetime.now(timezone.utc))
        now=datetime.now(timezone.utc)
        matching=match_odds(snapshot,schedule,now) if snapshot else dict(matched=[])
        matches=dict(generated_at=now.isoformat(),schedule_sources=sources,**matching)
        with threadpool_limits(limits=1):
            forecasts=forecast_games(history,schedule,metadata,model,now)
        report=None;availability_error=None
        try:report=collect_report(cache/'injuries',now)
        except (ValueError,OSError) as error:availability_error=str(error)
        # Fetch only teams with eligible pre-cutoff forecasts, not tomorrow's
        # full slate when no forecast can be made or a game is already too late.
        upcoming_ids={g['game_id'] for g in forecasts['predictions']}
        upcoming=[g for g in schedule if g['game_id'] in upcoming_ids]
        players=collect_players(current,upcoming,cache/'players',now) if report and upcoming else dict(games=[],players=[])
        now=datetime.now(timezone.utc)
        forecasts=apply_weight(adjust_forecasts(forecasts,report,players,current,now),DEFAULT_WEIGHT)
        forecasts['generated_at']=now.isoformat()
        for row in forecasts['predictions']:row['generated_at']=now.isoformat();row['inputs_as_of']=now.isoformat()
        for day in (today,today+timedelta(days=1)):
            edition=build_daily(matches,day.isoformat(),now,forecasts)
            save_snapshot(database,edition,now)
        put_state(database,'prediction-job-status',dict(completed_at=now.isoformat(),
            forecasts=len(forecasts['predictions']),market_status='unavailable' if market_error else 'available',
            availability_status='unavailable' if availability_error else 'available'),now)
        print('Prediction job complete:',len(forecasts['predictions']),'forecasts. Results settled in durable storage.',flush=True)
    finally:release_job(database)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,default=Path('artifacts/prediction-job-cache'))
    args=parser.parse_args();run(database_setting(),args.cache)
