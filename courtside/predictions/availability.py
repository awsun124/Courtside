"""Official injury-report ingestion and experimental player-absence correction.

The correction is a bounded heuristic, not a fitted causal player-impact model.
It never changes the frozen base checkpoint or its historical evaluation.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import time
import unicodedata

from .history import EASTERN, BASE_URL, fetch_bytes, parse_game, read_source, timestamp
from .odds_matching import TEAM_IDS

MODEL_VERSION = 'margin-strength-availability-v1'
REPORT_ROOT = 'https://ak-static.cms.nba.com/referee/injury/'
STATUSES = {'Out', 'Doubtful', 'Questionable', 'Probable', 'Available'}


def name_key(name):
    name = ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c)).lower()
    if ',' in name:
        parts = name.split(',')
        # Official names are Last, First; suffixes can be attached to Last.
        name = ' '.join(parts[1:])+' '+parts[0]
    name = re.sub(r'\b(jr|sr|ii|iii|iv)\b\.?', '', name)
    return re.sub('[^a-z0-9]', '', name)


def parse_report(raw, published_at):
    """Read table columns by header positions; carry sparse cells across pages."""
    import pdfplumber
    team_lookup = {re.sub('[^a-z]', '', name.lower()):'espn:'+value for name,value in TEAM_IDS.items()}
    entries, teams, seen = [], {}, set()
    current_date = matchup = team = None
    table_width = None
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=1, y_tolerance=2)
            headers = {w['text']:w for w in words if w['text'] in ('Team','Matchup','Reason')}
            # The table uses the same columns across pages, including continuations.
            has_header=all(k in headers for k in ('Team','Matchup','Reason'))
            if page.page_number==1 and not has_header:
                raise ValueError('Unrecognized official injury-report columns')
            if table_width is None:table_width=page.width
            if abs(page.width-table_width)>1:raise ValueError('Report page width changed')
            text = page.extract_text(x_tolerance=1)
            header = re.search(r'Injury\s+Report:\s*(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})\s*(AM|PM)', text)
            if not header:
                raise ValueError('Missing official report publication time')
            printed = datetime.strptime(' '.join(header.groups()), '%m/%d/%y %I:%M %p').replace(tzinfo=EASTERN)
            if printed != timestamp(published_at):
                raise ValueError('Report header time differs from source filename')
            groups = defaultdict(list)
            for w in words:
                if (headers['Team']['top']+10 if has_header else 75) < w['top'] < page.height-65:
                    groups[round(w['top']/2)*2].append(w)
            for _, line in sorted(groups.items()):
                columns = ['']*7
                # PDF layout widths are proportional; fail closed on unknown layouts.
                cuts = [0, .14*page.width, .235*page.width, .31*page.width, .50*page.width, .69*page.width, .79*page.width, page.width]
                for w in sorted(line,key=lambda w:w['x0']):
                    for i in range(7):
                        if cuts[i] <= w['x0'] < cuts[i+1]:
                            columns[i] += (' ' if columns[i] else '')+w['text']
                            break
                date_text, _, match_text, team_text, player, status, _ = columns
                if date_text:
                    if not re.fullmatch(r'\d{2}/\d{2}/\d{4}',date_text):
                        continue
                    current_date=datetime.strptime(date_text,'%m/%d/%Y').date().isoformat()
                if match_text:
                    if not re.fullmatch('[A-Z]{2,3}@[A-Z]{2,3}',match_text):
                        raise ValueError('Unrecognized report matchup')
                    matchup=match_text
                if team_text:
                    team=team_lookup.get(re.sub('[^a-z]','',team_text.lower()))
                    if team is None:raise ValueError('Unknown team in official report')
                if not current_date or not matchup or not team:
                    continue
                identity=(current_date,matchup,team)
                if 'NOT YET SUBMITTED' in ' '.join(columns):
                    teams[identity]='not_submitted'
                    continue
                if status:
                    if status not in STATUSES or not player or ',' not in player:
                        raise ValueError('Unrecognized injury status or player name')
                    key=(*identity,name_key(player))
                    if key in seen:raise ValueError('Duplicate official player status')
                    seen.add(key)
                    teams[identity]='submitted'
                    entries.append(dict(date=current_date,matchup=matchup,team_id=team,name=player,
                                        name_key=name_key(player),status=status))
    if not entries:raise ValueError('Official report contained no recognized player rows')
    return dict(published_at=timestamp(published_at).isoformat(),entries=entries,
                teams=[dict(date=d,matchup=m,team_id=t,status=s) for (d,m,t),s in teams.items()])


def fetch_pdf(url):
    response=subprocess.run(['curl','--disable','--silent','--compressed','--proto','=https','--max-time','20',
        '--connect-timeout','8','--max-filesize','5000000','--write-out','\n%{http_code}',url],capture_output=True,timeout=23)
    body,_,status=response.stdout.rpartition(b'\n')
    if response.returncode or status!=b'200' or not body.startswith(b'%PDF'):
        return None
    return body


def collect_report(folder, now, transport=fetch_pdf):
    """Probe at most eight quarter-hour official URLs, newest first, within two hours."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    local=now.astimezone(EASTERN)
    first=local.replace(minute=local.minute//15*15,second=0,microsecond=0)
    for offset in range(8):
        published=first-timedelta(minutes=15*offset)
        filename='Injury-Report_'+published.strftime('%Y-%m-%d_%I_%M%p')+'.pdf'
        path=folder/filename;meta=path.with_suffix('.meta.json');url=REPORT_ROOT+filename
        if path.exists() and meta.exists():
            raw=path.read_bytes();source=json.loads(meta.read_text())
            if hashlib.sha256(raw).hexdigest()!=source['sha256'] or source['url']!=url:
                raise ValueError('Injury report cache checksum mismatch')
        else:
            raw=transport(url)
            if raw is None:continue
            source=dict(url=url,published_at=published.isoformat(),retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(raw).hexdigest())
            path.write_bytes(raw);meta.write_text(json.dumps(source,indent=2)+'\n')
        report=parse_report(raw,published.isoformat());report['source']=source
        return report
    raise ValueError('No official injury report available in the last two hours')


def collect_players(games, upcoming, folder, now, window=10, sleep=time.sleep):
    """Collect only the last ten delayed games per upcoming team, cached by event."""
    teams={g[side+'_team_id'] for g in upcoming for side in ('home','away')}
    selected={}
    for team in teams:
        history=sorted((g for g in games if team in (g['home']['team_id'],g['away']['team_id'])
            and timestamp(g['scheduled_start'])+timedelta(hours=24)<=now),key=lambda g:timestamp(g['scheduled_start']))
        for g in history[-window:]:selected[g['game_id']]=g
    players,valid,sources,rejected=[],[],[],[]
    for i,game in enumerate(sorted(selected.values(),key=lambda g:timestamp(g['scheduled_start'])),1):
        try:
            key=game['game_id'].removeprefix('espn:')
            cached=Path(folder)/'raw'/f'summary-{key}.json'
            if not cached.exists():sleep(.25)
            payload,source=read_source('summary',key,folder)
            parsed,logs=parse_game(payload,key)
            if any(parsed[side]!=game[side] for side in ('home','away')) or timestamp(parsed['scheduled_start'])!=timestamp(game['scheduled_start']):
                raise ValueError('Player box score differs from audited result')
            valid.append(game);players.extend(logs);sources.append(source)
        except (ValueError,KeyError,TypeError,OSError) as error:
            rejected.append(dict(game_id=game['game_id'],reason=str(error)))
        if i%10==0 or i==len(selected):print('Player history',i,'/',len(selected),'rejected',len(rejected),flush=True)
    return dict(games=valid,players=players,sources=sources,rejected=rejected,collected_at=now.isoformat())


def player_value(logs):
    """Shrunk box-score Game Score per 36; proxy only, not PER or adjusted plus/minus."""
    total=sum(p['minutes'] for p in logs)
    value=sum(p['points']+.4*p['field_goals_made']-.7*p['field_goals_attempted']
        -.4*(p['free_throws_attempted']-p['free_throws_made'])+.4*p['rebounds']+p['steals']
        +.7*p['assists']+.7*p['blocks']-p['turnovers'] for p in logs)
    return (36*value+120*10)/(total+120)


def adjust_forecasts(forecasts, report, player_data, history, now):
    """Only confirmed Out statuses change probabilities. Other statuses are surfaced.

    Average actual minutes over the last five team games (including zero for
    absences) measures remaining exposure in recent team form. An already-absent
    player therefore has diminishing or zero incremental penalty.
    """
    from copy import deepcopy
    result=deepcopy(forecasts)
    result['base_model_version']=forecasts['model_version']
    result['model_version']=MODEL_VERSION
    history_map={g['game_id']:g for g in history}
    logs_by_team=defaultdict(list)
    for log in player_data['players']:
        if log['game_id'] not in history_map:raise ValueError('Unknown player result')
        logs_by_team[log['team_id']].append(log)
    valid_ids={g['game_id'] for g in player_data['games']}
    for row in result['predictions']:
        base=row['home_probability'];cutoff=timestamp(row['cutoff_at']);as_of=min(now,cutoff)
        note=dict(status='unavailable',base_home_probability=base,home_probability_change=0.,
            experimental=True,players=[],unmatched_players=[],pending_teams=[],report_url=report['source']['url'] if report else None,
            report_published_at=report['published_at'] if report else None)
        row['availability']=note
        if report is None or not timedelta(0)<=as_of-timestamp(report['published_at'])<=timedelta(hours=6):
            note['reason']='No current official injury report';continue
        date=timestamp(row['scheduled_start']).astimezone(EASTERN).date().isoformat()
        local_start=timestamp(row['scheduled_start']).astimezone(EASTERN)
        year=local_start.year-(local_start.month<10)
        season=f'{year}-{str(year+1)[-2:]}'
        teams=[row['home_team_id'],row['away_team_id']]
        report_teams={t['team_id']:t for t in report['teams'] if t['date']==date and t['team_id'] in teams}
        if len(report_teams)!=2:
            note['reason']='Official report does not cover both teams';continue
        if len({t['matchup'] for t in report_teams.values()})!=1:
            note['reason']='Official report matchup is ambiguous';continue
        matchup=next(iter(report_teams.values()))['matchup']
        penalty={team:0. for team in teams}
        for team in teams:
            if report_teams[team]['status']!='submitted':
                note['pending_teams'].append(team);continue
            recent=sorted((g for g in history if team in (g['home']['team_id'],g['away']['team_id'])
                and g['season']==season
                and timestamp(g['scheduled_start'])+timedelta(hours=24)<=as_of),key=lambda g:timestamp(g['scheduled_start']))[-10:]
            if len(recent)<5 or any(g['game_id'] not in valid_ids for g in recent):
                note['pending_teams'].append(team);continue
            ids={g['game_id'] for g in recent};last_five={g['game_id'] for g in recent[-5:]}
            grouped=defaultdict(list)
            for log in logs_by_team[team]:
                if log['game_id'] in ids:grouped[log['player_id']].append(log)
            profiles={pid:dict(name=logs[-1]['name'],value=player_value(logs),
                minutes=sum(p['minutes'] for p in logs if p['game_id'] in last_five)/5,
                ppg=sum(p['points'] for p in logs)/len(logs),games=len(logs)) for pid,logs in grouped.items()}
            # Replacement players are the lower-minute rotation, not a zero-value vacancy.
            bench=sorted((p for p in profiles.values() if p['minutes']>=5),key=lambda p:p['minutes'])[:5]
            replacement=sum(p['value'] for p in bench)/len(bench) if bench else 10.
            for injury in report['entries']:
                if injury['date']!=date or injury['team_id']!=team or injury['matchup']!=matchup:continue
                matches=[(pid,p) for pid,p in profiles.items() if name_key(p['name'])==injury['name_key']]
                if len(matches)!=1:
                    if injury['status']!='Available':note['unmatched_players'].append(injury['name'])
                    continue
                pid,p=matches[0]
                loss=0.
                if injury['status']=='Out' and p['games']>=3:
                    # Fixed, conservative conversion. No claim this is a learned injury coefficient.
                    loss=min(4.,max(0.,p['value']-replacement)*p['minutes']/36*.5)
                    penalty[team]+=loss
                if injury['status']!='Available':note['players'].append(dict(player_id=pid,name=p['name'],team_id=team,
                    status=injury['status'],recent_ppg=round(p['ppg'],1),recent_minutes=round(p['minutes'],1),
                    estimated_margin_impact=round(loss,3)))
        margin_change=min(6.,penalty[teams[1]])-min(6.,penalty[teams[0]])
        logit=math.log(base/(1-base))+.10*margin_change
        adjusted=1/(1+math.exp(-logit))
        row['home_probability']=adjusted
        note.update(status='applied' if abs(margin_change)>1e-12 else 'reviewed',
            home_probability_change=adjusted-base,estimated_home_margin_change=round(margin_change,3),
            reason='Confirmed absences adjusted using recent minutes and replacement value' if margin_change else 'No additional confirmed-absence adjustment')
        if len(note['pending_teams'])==2:
            note.update(status='unavailable',reason='Official reports or recent player history are not available for either team')
        if note['pending_teams'] or note['unmatched_players']:note['coverage']='partial'
        else:note['coverage']='complete'
        row['generated_at']=now.isoformat()
        row['inputs_as_of']=now.isoformat()
    return result
