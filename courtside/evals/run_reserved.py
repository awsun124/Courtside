"""Evaluate the reserved cases once against a recorded Git commit.

Use only after development is frozen. Seeing the results makes these cases
available for future development; they are no longer an untouched test set.
"""
from dataclasses import asdict
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from .run_news import evaluate_case,load_cases,write_report
from ..investigation import InvestigationSettings

ROOT=Path(__file__).resolve().parents[2]
FOLDER=Path(__file__).parent
FROZEN_FILES=['courtside/investigation.py','courtside/investigate.py','courtside/evals/run_news.py',
              'courtside/evals/run_reserved.py','courtside/evals/news_cases_reserved.json','courtside/evals/case_splits.json',
              'courtside/stories.py','scripts/validate_gdelt.py']


def verify_freeze(commit):
    resolved=subprocess.run(['git','rev-parse','--verify',commit+'^{commit}'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    hashes={}
    for name in FROZEN_FILES:
        frozen=subprocess.run(['git','show',resolved+':'+name],cwd=ROOT,capture_output=True,check=True).stdout
        current=(ROOT/name).read_bytes()
        if frozen!=current:raise ValueError('Commit does not match '+name)
        hashes[name]=hashlib.sha256(current).hexdigest()
    return resolved,hashes


def prepare_cases(suite):
    if not suite.get('cases') or any(case.get('split')!='reserved' for case in suite['cases']):
        raise ValueError('Expected reserved cases only')
    # Reuse schema checks, without changing the saved split or investigator input.
    validated=json.loads(json.dumps(suite))
    for case in validated['cases']:case['split']='development'
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'validate.json';path.write_text(json.dumps(validated))
        load_cases(path)
    return suite['cases']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-commit',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'courtside/artifacts/evals/reserved-first-run')
    args=parser.parse_args()
    commit,hashes=verify_freeze(args.freeze_commit)
    marker=FOLDER/'reserved_results.json'
    if marker.exists():parser.exit(2,'Reserved results already exist. Use development cases for further tuning.\n')
    path=FOLDER/'news_cases_reserved.json';raw=path.read_bytes()
    manifest=json.loads((FOLDER/'case_splits.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=manifest['files'][path.name]['sha256']:
        parser.exit(2,'Reserved cases differ from the recorded split.\n')
    cases=prepare_cases(json.loads(raw));settings=InvestigationSettings(provider='rules')
    with tempfile.TemporaryDirectory() as folder:
        config=Path(folder)/'rules.json';config.write_text(json.dumps(asdict(settings)))
        results=[evaluate_case(case,config,settings) for case in cases]
    report=dict(schema_version=1,report_title='Reserved news cases: first frozen run',frozen_commit=commit,created_at=datetime.now(timezone.utc).isoformat(),
        provider='rules',settings=asdict(settings),dataset_sha256=hashlib.sha256(raw).hexdigest(),code_sha256=hashes,
        scope='First run on reserved synthetic cases after freezing the code. Cases were visible to their author, not an independently authored blind test. Automatic passes are not semantic approval.',
        cases=len(results),automatic_passes=sum(r['automatic_status']=='pass' for r in results),
        automatic_failures=sum(r['automatic_status']=='fail' for r in results),semantic_reviews_pending=len(results),llm_calls=0,results=results)
    write_report(report,args.output)
    marker.write_text(json.dumps(report,indent=2)+'\n')
    print(f"Reserved cases: {report['automatic_passes']}/{len(results)} automatic passes; {len(results)} meaning reviews pending. Zero model calls.")
    return int(report['automatic_failures']>0)


if __name__=='__main__':raise SystemExit(main())
