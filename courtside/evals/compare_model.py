"""Compare an optional model with rules. Dry run by default; no CI model calls.

Execution needs --execute, a positive dollar limit, explicit token prices, and
an enabled server provider. Dollar reservations are conservative estimates using
the supplied prices, not a provider billing guarantee. Requests and output tokens
have hard per-run limits. Failed requests consume their reservation; no retries.
"""
from dataclasses import asdict,dataclass
from decimal import Decimal,InvalidOperation
import argparse
import hashlib
import json
import re
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from .run_news import DEFAULT_CASES,adapt_case,evaluate_case,grade_case,load_cases
from .rubrics import verify_rubrics
from ..investigate import add_investigations
from ..investigation import InvestigationSettings
from ..llm import ChatProvider

ROOT=Path(__file__).resolve().parents[2]


def amount(value):
    try: result=Decimal(str(value))
    except InvalidOperation:raise ValueError('Invalid price or budget') from None
    if not result.is_finite() or result<0 or result>Decimal('1000000'):raise ValueError('Prices and budgets must be finite and nonnegative')
    return result


@dataclass
class Budget:
    max_requests:int
    max_output_tokens:int
    max_usd:Decimal
    input_rate:Decimal
    output_rate:Decimal
    requests:int=0
    output_reserved:int=0
    dollars_reserved:Decimal=Decimal(0)
    stopped:bool=False

    def reserve(self,payload):
        output=payload['max_completion_tokens']
        # Deliberately overcount text bytes and add room for message framing.
        input_bound=len(json.dumps(payload).encode('utf-8'))*4+8192
        cost=(input_bound*self.input_rate+output*self.output_rate)/Decimal(1_000_000)
        if (self.stopped or self.requests>=self.max_requests or self.output_reserved+output>self.max_output_tokens
                or self.dollars_reserved+cost>self.max_usd):
            self.stopped=True
            raise ValueError('Evaluation budget exhausted before request')
        self.requests+=1;self.output_reserved+=output;self.dollars_reserved+=cost

    def report(self):
        return dict(requests_reserved=self.requests,output_tokens_reserved=self.output_reserved,
                    estimated_usd_reserved=str(self.dollars_reserved),stopped=self.stopped,
                    note='Reservations are not refunded after failures. Actual billing depends on provider prices and token accounting.')


class EvaluationProvider(ChatProvider):
    def __init__(self,settings,env_file,budget):
        super().__init__(settings,env_file)
        self.budget=budget

    def before_request(self,payload):
        self.budget.reserve(payload)


def compare(cases_path=DEFAULT_CASES,*,execute=False,max_cases=3,max_requests=15,
            output_tokens=512,max_usd='0',input_rate='0',output_rate='0',
            env_file=ROOT/'.env',provider_factory=EvaluationProvider):
    if type(max_cases) is not int or not 1<=max_cases<=33:raise ValueError('Choose 1–33 cases')
    if type(max_requests) is not int or not 1<=max_requests<=200:raise ValueError('Choose 1–200 requests')
    if type(output_tokens) is not int or not 128<=output_tokens<=1000:raise ValueError('Choose 128–1000 output tokens per request')
    suite=load_cases(cases_path);cases=suite['cases'][:max_cases]
    price_in,price_out,ceiling=amount(input_rate),amount(output_rate),amount(max_usd)
    if execute and (not ceiling or not price_in or not price_out):
        raise ValueError('Execution requires a positive budget and explicit input/output prices per million tokens')
    rubric_version=verify_rubrics(cases_path) if execute else dict(status='not_checked_in_dry_run')
    settings=InvestigationSettings(max_stories=1,max_llm_calls=5,max_output_tokens=output_tokens,max_seconds=30)
    report=dict(mode='execute' if execute else 'dry_run',provider='configured openai-compatible provider',
        rubrics=rubric_version,dataset=Path(cases_path).name,dataset_sha256=hashlib.sha256(Path(cases_path).read_bytes()).hexdigest(),
        code_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                     ('courtside/llm.py','courtside/investigate.py','courtside/investigation.py','courtside/evals/run_news.py','courtside/evals/compare_model.py')},
        settings=asdict(settings),case_ids=[c['id'] for c in cases],
        limits=dict(max_requests=max_requests,max_output_tokens_per_request=output_tokens,
                    max_output_tokens_reserved=max_requests*output_tokens,estimated_usd_limit=str(ceiling),
                    input_usd_per_million=str(price_in),output_usd_per_million=str(price_out)),
        scope='Development comparison of exact-excerpt investigators. Automatic checks are not meaning review or proof that a model is better.',
        results=[])
    if not execute:
        report['note']='No provider settings read, no model initialized, and no requests made. Add --execute with explicit limits to run later.'
        return report
    budget=Budget(max_requests,max_requests*output_tokens,ceiling,price_in,price_out)
    with tempfile.TemporaryDirectory() as folder:
        baseline=Path(folder)/'rules.json';baseline.write_text(json.dumps(asdict(settings)))
        model_settings=InvestigationSettings(**{**asdict(settings),'provider':'openai-compatible'})
        config=Path(folder)/'model.json';config.write_text(json.dumps(asdict(model_settings)))
        for case in cases:
            row=dict(id=case['id'],rules=evaluate_case(case,baseline,settings))
            if budget.stopped or budget.requests>=budget.max_requests:
                row['model']=dict(status='not_run_budget',review='pending')
                report['results'].append(row);continue
            providers=[]
            def factory(current,env_file):
                provider=provider_factory(current,env_file,budget);providers.append(provider);return provider
            result,briefing=adapt_case(case)
            from copy import deepcopy
            before=deepcopy(briefing);started=time.perf_counter()
            with patch('courtside.investigate.ChatProvider',side_effect=factory):
                output=add_investigations(result,briefing,[],settings_file=config,env_file=env_file)
            checks=grade_case(case,before,output,model_settings,model_calls_allowed=True)
            failed=not all(checks.values())
            usage=dict(prompt_tokens=0,completion_tokens=0,reported_calls=0)
            for provider in providers:
                for name in usage:usage[name]+=getattr(provider,'token_usage',{}).get(name,0)
            attempts=sum(getattr(p,'calls_made',0) for p in providers)
            source_status=output.get('investigation',{}).get('status')
            status = 'budget_exhausted' if budget.stopped else 'not_run_source_restricted' if not providers and source_status=='source_restricted' else 'not_run_configuration' if not providers and source_status=='unavailable' else 'completed'
            reported_cost = str((usage['prompt_tokens']*price_in+usage['completion_tokens']*price_out)/Decimal(1_000_000)) if usage['reported_calls']==attempts else None
            model_id=None
            if providers:
                candidate=getattr(providers[0],'model',None)
                secret=getattr(providers[0],'api_key','')
                if isinstance(candidate,str) and re.fullmatch(r'[A-Za-z0-9._:/-]{1,200}',candidate) and not (secret and secret in candidate):model_id=candidate
            row['model']=dict(status=status,model_id=model_id,
                automatic_status='fail' if failed else 'pass',checks=checks,
                elapsed_seconds=round(time.perf_counter()-started,6),requests=attempts,
                token_usage=usage,usage_complete=usage['reported_calls']==attempts,reported_token_cost_usd=reported_cost,
                review=dict(status='pending',required_meaning=case['expected']['required_meaning'],forbidden_meaning=case['expected']['forbidden_meaning']),output=output)
            report['results'].append(row)
    report['budget']=budget.report()
    completed=[r for r in report['results'] if r['model']['status']=='completed']
    report['comparison']=dict(completed_pairs=len(completed),
        rules_automatic_passes=sum(r['rules']['automatic_status']=='pass' for r in completed),
        model_automatic_passes=sum(r['model']['automatic_status']=='pass' for r in completed),
        not_compared_cases=len(cases)-len(completed),meaning_review='pending; do not choose a winner from automatic checks alone')
    return report


def write_comparison(report,folder):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Rules and model comparison','',report['scope'],'',f"Mode: {report['mode']}. Cases selected: {len(report['case_ids'])}.",'']
    if report['mode']=='dry_run':lines.append(report['note'])
    else:
        lines+=['| Case | Rules checks | Model checks | Model requests |','| --- | --- | --- | --- |']
        for row in report['results']:
            model=row['model'];lines.append(f"| {row['id']} | {row['rules']['automatic_status']} | {model.get('automatic_status',model['status'])} ({model['status']}) | {model.get('requests',0)} |")
        lines+=['',f"Estimated cost reserved: ${report['budget']['estimated_usd_reserved']}. Meaning review is still pending."]
    (folder/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--cases',type=Path,default=DEFAULT_CASES)
    parser.add_argument('--max-cases',type=int,default=3)
    parser.add_argument('--max-requests',type=int,default=15)
    parser.add_argument('--output-tokens',type=int,default=512)
    parser.add_argument('--max-usd',default='0')
    parser.add_argument('--input-usd-per-million',default='0')
    parser.add_argument('--output-usd-per-million',default='0')
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--output',type=Path,default=ROOT/'courtside/artifacts/evals/model-comparison')
    args=parser.parse_args()
    try:
        report=compare(args.cases,execute=args.execute,max_cases=args.max_cases,max_requests=args.max_requests,
          output_tokens=args.output_tokens,max_usd=args.max_usd,input_rate=args.input_usd_per_million,
          output_rate=args.output_usd_per_million,env_file=args.env_file)
        write_comparison(report,args.output)
    except (ValueError,KeyError,TypeError,OSError) as error:
        parser.exit(2,f'Comparison could not start ({type(error).__name__}). Check inputs and limits.\n')
    print('Dry run only; no model calls.' if not args.execute else 'Comparison saved. Review outputs before drawing conclusions.')
    if args.execute:
        return int(report['comparison']['not_compared_cases']>0 or any(row['model'].get('automatic_status')=='fail' for row in report['results']))
    return 0


if __name__=='__main__':raise SystemExit(main())
