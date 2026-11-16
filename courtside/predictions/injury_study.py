"""Retrospective injury-weight selection on January and paired 501-game evaluation."""

import argparse
import csv
from copy import deepcopy
from collections import Counter
from datetime import datetime,timezone
import json
import math
import io
from pathlib import Path

from .availability import adjust_forecasts,collect_report
from .baseline import scores
from .history import timestamp,EASTERN,read_source,parse_game
from .holdout import paired_interval
from .improve import ROOT,save_new,digest
from .player_bulk import cache_csv,import_bulk

OUTPUT=ROOT/'courtside/artifacts/predictions/injury-evaluation'
MODEL=ROOT/'courtside/artifacts/predictions/improved-v2'


def repair_player_coverage(folder):
    """Remove only anonymous, statless archive placeholders; reconcile every score."""
    verify(folder)
    original=folder/'players/raw-bulk/player_box_2026.csv'
    filtered=folder/'players/reconciled-player-box.csv'
    discarded=[]
    numeric=['minutes','points','field_goals_made','field_goals_attempted','three_point_field_goals_made',
        'three_point_field_goals_attempted','free_throws_made','free_throws_attempted','rebounds','assists',
        'steals','blocks','turnovers','fouls','offensive_rebounds','defensive_rebounds','plus_minus']
    buffer=io.StringIO(newline='')
    with original.open() as src:
        reader=csv.DictReader(src);writer=csv.DictWriter(buffer,fieldnames=reader.fieldnames);writer.writeheader()
        for row in reader:
            if row['athlete_id']=='':
                if row['athlete_display_name'] or any(row[k] not in ('','0') for k in numeric):
                    raise ValueError('Anonymous player has statistics; cannot safely exclude')
                discarded.append(dict(game_id=row['game_id'],team_id=row['team_id'],reason='Anonymous zero-stat placeholder'))
            else:writer.writerow(row)
    if filtered.exists():
        if filtered.read_bytes()!=buffer.getvalue().encode():raise ValueError('Filtered cache changed')
    else:filtered.write_bytes(buffer.getvalue().encode())
    sources=json.loads((folder/'sources.json').read_text())['files']
    paths=[Path(p) for p in sources if p.endswith('results-2025-10-01-2026-01-31.json') or p.endswith('results-2026-02-01-2026-04-12.json')]
    games=[g for p in paths for g in json.loads(p.read_text())['games']]
    dataset=import_bulk(dict(coverage=dict(complete=True),games=games),[filtered],[dict(original_sha256=digest(original),
        filtered_sha256=digest(filtered),excluded_placeholders=discarded)])
    game_map={g['game_id']:g for g in games};repairs=[];unresolved=[]
    for rejected in dataset['coverage']['rejected']:
        game=game_map[rejected['game_id']];key=game['game_id'].removeprefix('espn:')
        payload,source=read_source('summary',key,folder/'players/direct-repairs')
        payload=deepcopy(payload)
        for team in payload['boxscore']['players']:
            for table in team['statistics']:
                cleaned=[]
                for athlete in table['athletes']:
                    if not athlete['athlete'].get('id'):
                        if any(v not in ('--','0','0-0','') for v in athlete['stats']):
                            raise ValueError('Anonymous direct player has nonzero stats')
                    else:cleaned.append(athlete)
                table['athletes']=cleaned
        try:parsed,logs=parse_game(payload,key)
        except ValueError as error:
            unresolved.append(dict(game_id=game['game_id'],reason=str(error)));continue
        if parsed!=game:raise ValueError('Direct repaired result differs from audited history')
        dataset['games'].append(game);dataset['players'].extend(logs)
        repairs.append(dict(game_id=game['game_id'],source=source))
    dataset['coverage'].update(selected_complete=not unresolved,full_history_complete=not unresolved,validated_games=len(dataset['games']),
        player_rows=len(dataset['players']),rejected=unresolved,direct_repairs=repairs,excluded_placeholders=discarded)
    save_new(folder/'reconciled-player-history.json',dataset)
    print('Reconciled',len(dataset['games']),'of',len(games),'games; removed',len(discarded),
          'anonymous zero-stat rows; unresolved',len(unresolved),flush=True)


def initialize(folder):
    plan=json.loads(Path(__file__).with_name('injury_study_plan.json').read_text())
    save_new(folder/'plan.json',plan)
    january=ROOT/'artifacts/predictions/fanduel-2026-01/results/results-2025-10-01-2026-01-31.json'
    final=MODEL/'final-results/results-2026-02-01-2026-04-12.json'
    games=json.loads(january.read_text())['games']+json.loads(final.read_text())['games']
    path,source=cache_csv(2026,folder/'players')
    players=import_bulk(dict(coverage=dict(complete=True),games=games),[path],[source])
    save_new(folder/'player-history.json',players)
    print('Player coverage',players['coverage'],flush=True)
    validation=json.loads((MODEL/'development-predictions.json').read_text())
    test=json.loads((MODEL/'test-report.json').read_text())['predictions']
    for phase,rows in [('validation',validation),('test',test)]:
        rows=[r for r in rows if plan[phase+'_start']<=timestamp(r['scheduled_start']).astimezone(EASTERN).date().isoformat()<plan[phase+'_end_exclusive']]
        save_new(folder/(phase+'-base.json'),rows)
        print(phase,len(rows),flush=True)
    save_new(folder/'sources.json',dict(created_at=datetime.now(timezone.utc).isoformat(),
        files={str(p):digest(p) for p in [january,final,MODEL/'development-predictions.json',MODEL/'test-report.json',
             folder/'plan.json',folder/'player-history.json',folder/'validation-base.json',folder/'test-base.json']}))


def verify(folder):
    sources=json.loads((folder/'sources.json').read_text())
    for file,sha in sources['files'].items():
        if digest(file)!=sha:raise ValueError('Frozen study input changed: '+file)
    return json.loads((folder/'plan.json').read_text())


def collect(folder,phase):
    verify(folder)
    if phase=='test' and not (folder/'selection.json').exists():raise ValueError('Select on validation first')
    rows=json.loads((folder/(phase+'-base.json')).read_text())
    cutoffs=sorted({r['cutoff_at'] for r in rows})
    out=folder/(phase+'-reports');out.mkdir(exist_ok=True)
    for i,cutoff in enumerate(cutoffs,1):
        destination=out/(timestamp(cutoff).strftime('%Y%m%dT%H%M%SZ')+'.json')
        if destination.exists():continue
        try:
            report=collect_report(folder/'reports',timestamp(cutoff))
            saved=dict(cutoff_at=cutoff,report=report,error=None)
        except (ValueError,OSError) as error:
            saved=dict(cutoff_at=cutoff,report=None,error=str(error))
        save_new(destination,saved)
        if i%5==0 or i==len(cutoffs):print(phase,'reports',i,'/',len(cutoffs),'latest',cutoff,'available',saved['report'] is not None,flush=True)


def adjustment_rows(folder,phase):
    rows=json.loads((folder/(phase+'-base.json')).read_text());players=json.loads((folder/'reconciled-player-history.json').read_text())
    # The independent history includes all validated team results, not only box-score-covered games.
    sources=json.loads((folder/'sources.json').read_text())['files']
    paths=[Path(p) for p in sources if p.endswith('results-2025-10-01-2026-01-31.json') or p.endswith('results-2026-02-01-2026-04-12.json')]
    history=[g for path in paths for g in json.loads(path.read_text())['games']]
    by_id={g['game_id']:g for g in history};out=[]
    for row in rows:
        cutoff=timestamp(row['cutoff_at']);game=by_id[row['game_id']]
        path=folder/(phase+'-reports')/(cutoff.strftime('%Y%m%dT%H%M%SZ')+'.json')
        saved=json.loads(path.read_text())
        fixture=dict(game_id=row['game_id'],scheduled_start=row['scheduled_start'],cutoff_at=row['cutoff_at'],
            home_team_id=game['home']['team_id'],away_team_id=game['away']['team_id'],home_probability=row['model_probability'])
        forecasts=dict(model_version='margin-strength-v1',predictions=[fixture])
        adjusted=adjust_forecasts(forecasts,saved['report'],players,history,cutoff)['predictions'][0]
        note=adjusted['availability'];shift=note.get('estimated_home_margin_change',0)*.10
        out.append(dict(game_id=row['game_id'],scheduled_start=row['scheduled_start'],cutoff_at=row['cutoff_at'],target=row['target'],
            base_probability=row['model_probability'],injury_logit_shift=shift,availability=note,
            report_cache_sha256=digest(path)))
    return out


def probability(row,weight):
    p=row['base_probability'];return 1/(1+math.exp(-(math.log(p/(1-p))+weight*row['injury_logit_shift'])))


def select(folder):
    plan=verify(folder)
    rows=adjustment_rows(folder,'validation');labels=[r['target'] for r in rows]
    trials=[dict(weight=w,**scores(labels,[probability(r,w) for r in rows])) for w in plan['weight_candidates']]
    winner=min(trials,key=lambda t:(-t['accuracy'],t['log_loss'],t['weight']))
    save_new(folder/'validation-adjustments.json',rows)
    code_files=['injury_study.py','availability.py']
    selection=dict(created_at=datetime.now(timezone.utc).isoformat(),weight=winner['weight'],trials=trials,
        validation_coverage=dict(Counter(r['availability']['status'] for r in rows)),
        sources_sha256=digest(folder/'sources.json'),validation_sha256=digest(folder/'validation-adjustments.json'),
        player_history_sha256=digest(folder/'reconciled-player-history.json'),
        code_sha256={name:digest(Path(__file__).with_name(name)) for name in code_files})
    save_new(folder/'selection.json',selection)
    print(json.dumps(selection,indent=2),flush=True)


def test(folder):
    verify(folder);selection=json.loads((folder/'selection.json').read_text())
    for name,sha in selection['code_sha256'].items():
        if digest(Path(__file__).with_name(name))!=sha:raise ValueError('Frozen injury implementation changed')
    if digest(folder/'sources.json')!=selection['sources_sha256'] or digest(folder/'validation-adjustments.json')!=selection['validation_sha256']:
        raise ValueError('Frozen validation changed')
    if digest(folder/'reconciled-player-history.json')!=selection['player_history_sha256']:
        raise ValueError('Reconciled player source changed')
    rows=adjustment_rows(folder,'test');labels=[r['target'] for r in rows]
    if len(rows)!=501:raise ValueError('Must retain every original test game')
    for row in rows:
        row['adjusted_probability']=probability(row,selection['weight'])
        row['default_adjustment_probability']=probability(row,1)
    report=dict(weight=selection['weight'],selection_sha256=digest(folder/'selection.json'),
        metrics={key:scores(labels,[r[key] for r in rows]) for key in ('base_probability','adjusted_probability','default_adjustment_probability')},
        coverage=dict(Counter(r['availability']['status'] for r in rows)),
        adjusted_minus_base_brier_95_interval=paired_interval(rows,'adjusted_probability','base_probability',42),
        changed_picks=sum((r['base_probability']>=.5)!=(r['adjusted_probability']>=.5) for r in rows),
        predictions=rows,evaluated_at=datetime.now(timezone.utc).isoformat(),
        limitations=['Retrospective comparison on previously inspected base-model test games.',
            'Official reports missing at cutoff leave the base prediction intact.',
            'Box-score proxy and injury-weight selection may not generalize; no confirmed starting lineups.'])
    save_new(folder/'test-report.json',report)
    lines=['# Injury-adjustment evaluation','',f"January-selected weight: {selection['weight']}",'',
        '| Model | Accuracy | Brier | Log loss |','| --- | ---: | ---: | ---: |']
    for key,s in report['metrics'].items():lines.append(f"| {key} | {s['accuracy']:.2%} ({round(s['accuracy']*s['games'])}/{s['games']}) | {s['brier']:.5f} | {s['log_loss']:.5f} |")
    lines+=['', 'All 501 original February 1–April 12, 2026 games retained.',
        'Availability coverage: '+json.dumps(report['coverage']),
        'Brier difference interval: '+str(report['adjusted_minus_base_brier_95_interval']),
        '',*report['limitations']]
    (folder/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='predictions'},indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['initialize','repair-player-coverage','collect-validation','select','collect-test','test'])
    parser.add_argument('--folder',type=Path,default=OUTPUT);args=parser.parse_args()
    if args.phase=='initialize':initialize(args.folder)
    elif args.phase=='repair-player-coverage':repair_player_coverage(args.folder)
    elif args.phase.startswith('collect-'):collect(args.folder,args.phase.split('-')[1])
    elif args.phase=='select':select(args.folder)
    else:test(args.folder)
