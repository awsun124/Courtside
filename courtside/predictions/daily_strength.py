"""Collect current results and save independent-model daily predictions."""

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

from threadpoolctl import threadpool_limits

from .daily import build_daily, save_daily
from .forecast_strength import load_frozen, forecast_games
from .history import BASE_URL, EASTERN, fetch_bytes, timestamp
from .improve import ROOT, DEFAULT_OUTPUT, load_development, save_new, digest
from .odds_matching import parse_schedule, build_report
from .availability import collect_report, collect_players, adjust_forecasts
from .injury_calibration import DEFAULT_WEIGHT,load_weight,apply_weight


def run(output, model_folder, daily_output, odds_folder=None, availability=True):
    selection, model = load_frozen(model_folder)
    now = datetime.now(timezone.utc)
    today = now.astimezone(EASTERN).date()
    year = today.year if today.month >= 10 else today.year-1
    start = today.replace(year=year, month=10, day=1)
    spec = importlib.util.spec_from_file_location('result_collector', ROOT/'scripts/collect_prediction_results.py')
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    coverage = collector.collect_results(start.isoformat(), (today-timedelta(days=1)).isoformat(), output/'results')
    if not coverage['complete']:
        raise ValueError('Current results are incomplete; no forecasts saved')
    current_path = output/'results'/f'results-{start}-{today-timedelta(days=1)}.json'
    current = json.loads(current_path.read_text())
    games, _ = load_development()
    previous_path = model_folder/'final-results/results-2026-02-01-2026-04-12.json'
    previous = json.loads(previous_path.read_text())
    if not previous['coverage']['complete']:
        raise ValueError('Previous-season results incomplete')
    games += previous['games']+current['games']
    if odds_folder:
        matches = build_report(odds_folder, datetime.now(timezone.utc))
    else:
        sources=[]
        for day in (today,today+timedelta(days=1)):
            url=BASE_URL+'scoreboard?dates='+day.strftime('%Y%m%d')
            payload=json.loads(fetch_bytes(url))
            sources.append(dict(url=url,retrieved_at=datetime.now(timezone.utc).isoformat(),raw=payload))
        matches=dict(generated_at=datetime.now(timezone.utc).isoformat(),matched=[],schedule_sources=sources)
    upcoming=[]
    for source in matches['schedule_sources']:
        rows,rejected=parse_schedule(source['raw'])
        if rejected:raise ValueError('Invalid current schedule')
        upcoming.extend(rows)
    now=datetime.now(timezone.utc)
    with threadpool_limits(limits=1):
        forecasts=forecast_games(games,upcoming,selection,model,now)
    if availability:
        report=None
        try:
            report=collect_report(output/'availability/reports',now)
        except (ValueError,OSError) as error:
            print('Official availability unavailable:',str(error),flush=True)
        players=collect_players(current['games'],upcoming,output/'availability/players',now) if report else dict(games=[],players=[])
        now=datetime.now(timezone.utc)
        forecasts=adjust_forecasts(forecasts,report,players,current['games'],now)
        calibration=ROOT/'courtside/artifacts/predictions/injury-evaluation'
        weight=DEFAULT_WEIGHT
        if (calibration/'selection.json').exists():
            weight,calibration_hash=load_weight(calibration)
            forecasts['injury_calibration_sha256']=calibration_hash
        forecasts=apply_weight(forecasts,weight)
        forecasts['generated_at']=now.isoformat()
        for row in forecasts['predictions']:row['generated_at']=now.isoformat()
    run_folder=output/now.strftime('%Y%m%dT%H%M%S%fZ')
    forecasts['sources']=dict(selection_sha256=digest(model_folder/'selection.json'),current_history_sha256=digest(current_path))
    save_new(run_folder/'forecasts.json',forecasts)
    save_new(run_folder/'matches.json',matches)
    if availability:
        save_new(run_folder/'availability-report.json',report)
        save_new(run_folder/'player-history.json',players)
    for day in (today,today+timedelta(days=1)):
        report=build_daily(matches,day.isoformat(),now,forecasts)
        report['sources']=dict(forecasts_sha256=digest(run_folder/'forecasts.json'))
        print('Saved',save_daily(daily_output,report),flush=True)
    print(f"Generated {len(forecasts['predictions'])} independent forecasts; {len(forecasts['excluded'])} excluded.",flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'courtside/artifacts/predictions/current-strength')
    parser.add_argument('--model',type=Path,default=DEFAULT_OUTPUT.with_name('improved-v2'))
    parser.add_argument('--daily-output',type=Path,default=ROOT/'courtside/artifacts/predictions/availability-daily')
    parser.add_argument('--odds-folder',type=Path,help='Optional saved odds cache; no odds requests are made')
    parser.add_argument('--base-only',action='store_true',help='Generate the original evaluated model without the experimental injury adjustment')
    args=parser.parse_args()
    run(args.output,args.model,args.daily_output,args.odds_folder,not args.base_only)
