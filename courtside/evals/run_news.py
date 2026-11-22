"""Run fictional news test cases without network access: python -m courtside.evals.run_news.

Automatic checks verify sources and tool use. A reviewer still needs to check
whether each answer means the right thing. This runner makes no model calls.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from unittest.mock import patch

from ..investigate import add_investigations
from ..investigation import InvestigationSettings, evidence_id, short_text

DEFAULT_CASES = Path(__file__).with_name('news_cases.json')
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / 'artifacts/evals/news'


def load_cases(path):
    suite = json.loads(Path(path).read_text())
    if suite.get('schema_version') != 1 or not suite.get('cases'):
        raise ValueError('Expected schema version 1 and nonempty cases')
    seen = set()
    for case in suite['cases']:
        if case['id'] in seen or case['split'] != 'development':
            raise ValueError('Case IDs must be unique; this runner accepts development cases only')
        seen.add(case['id'])
        articles = case['input']['articles']
        ids = {a['id'] for a in articles}
        urls = {a['url'] for a in articles}
        expected = case['expected']
        if len(ids) != len(articles) or len(urls) != len(articles):
            raise ValueError('Article IDs and URLs must be unique within a case')
        target = set(case['input']['target_article_ids'])
        if not target or not target <= ids:
            raise ValueError('Missing or unknown target articles')
        if not set(expected['allowed_citation_article_ids']) <= target:
            raise ValueError('Citation expectations must refer to target articles')
        if not set(expected['quarantine_article_ids']) <= ids:
            raise ValueError('Unknown quarantine article')
        if expected['summary'] not in {'supported_excerpts', 'insufficient_evidence', 'supported_excerpts_or_insufficient_evidence'}:
            raise ValueError('Unknown summary expectation')
        if not expected['required_meaning'] or not expected['forbidden_meaning']:
            raise ValueError('Each case needs a checklist for reviewing its meaning')
    return suite


def adapt_case(case):
    """Pass articles to the investigator without exposing the expected answers.

    Reporting groups and scores come from the test data. This checks the
    investigator, not article grouping, scoring or answers to user questions.
    """
    supplied = deepcopy(case['input'])
    articles = supplied['articles']
    target = set(supplied['target_article_ids'])
    groups = {}
    for article in articles:
        article['canonical_url'] = article['url']
        article['features'] = dict(teams=['fixture-team'], people=[])
        if article['id'] in target:
            groups.setdefault(article['reporting_group'], []).append(article)
    targets = [a for a in articles if a['id'] in target]
    urls = [a['url'] for a in targets]
    text = ' '.join(a.get('description') or a['title'] for a in targets).lower()
    story = dict(id=case['id'], title=targets[0]['title'], event='news', article_urls=urls,
                 evidence_urls=urls, reporting_groups=[dict(evidence_urls=[a['url'] for a in members],
                 publishers=[a['publisher_name'] for a in members], first_published_at=members[0].get('published_at'))
                 for members in groups.values()], has_rumor=any(word in text for word in ('reportedly', 'rumor', 'speculation')),
                 score=50, rank=1, breakdown=dict(coverage=dict(independent_publishers=len(groups))))
    related = [dict(id='related-'+a['id'], title=a['title'], event='news', article_urls=[a['url']])
               for a in articles if a['id'] not in target]
    result = dict(articles=articles, stories=[deepcopy(story), *related])
    briefing = dict(data_mode='sample', as_of=supplied['as_of'], candidates=[deepcopy(story)], stories=[deepcopy(story)])
    return result, briefing


def grade_case(case, before, output, settings, model_calls_allowed=False):
    story = output['candidates'][0]
    record = story.get('investigation', {})
    summary = story.get('summary')
    citations = (summary or {}).get('citations', [])
    articles = {evidence_id(a['url']): a for a in case['input']['articles']}
    expected = case['expected']
    allowed = {evidence_id(a['url']) for a in articles.values() if a['id'] in expected['allowed_citation_article_ids']}
    references = {r['evidence_id']: r for r in record.get('evidence_references', [])}
    activity = record.get('activity', [])
    retrieved = {eid for step in activity if step['tool'] == 'retrieve_material' and step['status'] == 'ok'
                 for eid in step.get('evidence_ids', [])}
    checks = {}
    checks['completed_without_error'] = record.get('status') in {'complete', 'insufficient_evidence', 'source_restricted'}
    checks['expected_summary_presence'] = (bool(citations) if expected['summary'] == 'supported_excerpts'
                                         else not summary if expected['summary'] == 'insufficient_evidence' else True)
    def valid_citation(citation):
        eid = citation.get('evidence_id')
        article = articles.get(eid)
        return bool(article and eid in allowed and eid in retrieved and eid in references
            and not references[eid].get('quarantined') and article['investigation_allowed']
            and citation.get('url') == article['url'] and citation.get('publisher') == article['publisher_name']
            and isinstance(citation.get('quote'), str) and 5 <= len(citation['quote']) <= 300
            and any(citation['quote'] in short_text(article.get(field)) for field in ('title','description','content_excerpt')))
    checks['citations_retrieved_permitted_and_supported'] = all(valid_citation(c) for c in citations)
    def source_time(value):
        if not isinstance(value,str): return None
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            return parsed if parsed.tzinfo is not None else None
        except ValueError: return None
    checks['citation_dates_match_sources'] = all(
        citation.get('evidence_id') in articles and
        (source_time(citation.get('published_at')) == source_time(articles[citation['evidence_id']].get('published_at')))
        and (source_time(citation.get('published_at')) is not None or citation.get('published_at') is None)
        for citation in citations)
    # Verify the displayed summary actually consists of its validated citations.
    lines = (summary or {}).get('text', '').removeprefix('Unconfirmed report. ').splitlines()
    def supported_line(line, citation):
        prefix = f"{citation.get('publisher')} reports: {citation.get('quote')}"
        return line in {prefix, prefix+'…'}
    display_citations = {}
    for citation in citations:
        key = ' '.join(str(citation.get('quote', '')).split())
        display_citations.setdefault(key, citation)
    checks['display_matches_cited_excerpts'] = len(lines) == len(display_citations) and all(
        supported_line(line,c) for line,c in zip(lines,display_citations.values()))
    checks['quarantine_expectations'] = all(
        (eid := evidence_id(a['url'])) not in {c.get('evidence_id') for c in citations}
        and (eid not in retrieved or eid in references and references[eid].get('quarantined') is True)
        for a in articles.values() if a['id'] in expected['quarantine_article_ids'])
    checks['restricted_sources_not_retrieved'] = all(a['investigation_allowed'] for eid,a in articles.items() if eid in retrieved)
    count = expected['retrieved_reporting_groups']
    eligible = {eid for eid,a in articles.items() if a['id'] in case['input']['target_article_ids']
                and a['investigation_allowed'] and a['id'] not in expected['quarantine_article_ids']}
    usable = {eid for eid in retrieved & eligible if eid in references and not references[eid].get('quarantined')}
    actual_groups = {articles[eid]['reporting_group'] for eid in usable}
    reported_count = record.get('corroboration', {}).get('retrieved_reporting_groups', 0)
    checks['reporting_group_count'] = reported_count == len(actual_groups) and (
        count is None or not eligible <= retrieved or reported_count == count)
    expected_conflict = expected.get('availability_conflict')
    if expected_conflict:
        checks['availability_conflict_signal'] = record.get('conflicts', {}).get('status', 'not_detected') == expected_conflict
        if expected_conflict == 'detected':
            checks['conflicts_not_corroboration'] = record.get('corroboration', {}).get('status') == 'conflicting_reports'
            checks['conflict_warning'] = any('Sources disagree' in warning for warning in record.get('uncertainty', []))
    permitted = {'inspect_related_stories','compare_previous_briefings','search_articles','retrieve_material','finish'}
    checks['only_permitted_successful_tools'] = all(step['tool'] in permitted and step['status'] == 'ok' for step in activity)
    checks['rankings_unchanged'] = all(
        [(s['id'], s['rank'], s['score'], s['breakdown']) for s in output[field]] ==
        [(s['id'], s['rank'], s['score'], s['breakdown']) for s in before[field]] for field in ('stories','candidates'))
    stats = output.get('investigation', {})
    if model_calls_allowed:
        checks['model_calls_and_steps_within_limits'] = 0 <= stats.get('llm_calls', 0) <= settings.max_llm_calls and stats.get('steps_used',0) <= settings.max_steps
    else:
        checks['zero_model_calls_and_bounded_steps'] = stats.get('llm_calls', 0) == 0 and stats.get('steps_used',0) <= settings.max_steps
    return checks


def evaluate_case(case, settings_file, settings):
    result, briefing = adapt_case(case)
    before = deepcopy(briefing)
    started = time.perf_counter()
    try:
        # Block subprocesses and sockets so this run cannot call a model or the network.
        with patch.object(subprocess, 'run', side_effect=RuntimeError('Offline evaluation blocks subprocess calls')), \
             patch.object(socket, 'socket', side_effect=RuntimeError('Offline evaluation blocks sockets')):
            output = add_investigations(result, briefing, [], settings_file=settings_file)
        checks = grade_case(case, before, output, settings)
        error = None
    except Exception as exc:
        output = None
        checks = dict(completed_without_error=False)
        error = type(exc).__name__  # Save the error type without copying potentially private error details.
    return dict(id=case['id'], category=case['category'], automatic_status='pass' if all(checks.values()) else 'fail',
                checks=checks, elapsed_seconds=round(time.perf_counter()-started, 6), error=error,
                review=dict(status='pending', task=case['task'], required_meaning=case['expected']['required_meaning'],
                            forbidden_meaning=case['expected']['forbidden_meaning']), output=output)


def run_suite(cases_path=DEFAULT_CASES):
    suite = load_cases(cases_path)
    real_reporting = suite.get('provenance', {}).get('kind') == 'saved_public_reporting'
    settings = InvestigationSettings(provider='rules')
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder)/'rules.json'
        config.write_text(json.dumps(asdict(settings)))
        results = [evaluate_case(case, config, settings) for case in suite['cases']]
    files = [Path(__file__), Path(__file__).parents[1]/'investigation.py', Path(__file__).parents[1]/'investigate.py']
    return dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(), provider='rules',
                dataset_sha256=hashlib.sha256(Path(cases_path).read_bytes()).hexdigest(),
                code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}, settings=asdict(settings),
                report_title='Saved news reporting baseline' if real_reporting else 'News investigator development baseline',
                scope=('Saved public reporting development cases. Labels check fidelity to saved excerpts, not independent factual truth.' if real_reporting else 'Fictional development cases.') + ' Automatic checks cover sources and tool use, not whether each answer means the right thing. These are not results on unseen cases.',
                cases=len(results), automatic_passes=sum(r['automatic_status']=='pass' for r in results),
                automatic_failures=sum(r['automatic_status']=='fail' for r in results),
                semantic_reviews_pending=len(results), llm_calls=0, results=results)


def write_report(report, folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    lines=['# '+report.get('report_title','News investigator development baseline'), '', report['scope'], '',
           f"Automatic checks: {report['automatic_passes']}/{report['cases']} cases passed. Meaning review pending: {report['semantic_reviews_pending']}. LLM calls: 0.", '',
           '| Case | Automatic checks | Failed checks | Meaning review |', '| --- | --- | --- | --- |']
    for result in report['results']:
        failed=', '.join(k for k,v in result['checks'].items() if not v) or 'None'
        lines.append(f"| {result['id']} | {result['automatic_status']} | {failed} | Pending |")
    lines+=['', 'Review each answer and its tool calls in results.json using the included checklist. Passing automatic checks does not mean the answer has passed meaning review.']
    (folder/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=DEFAULT_CASES)
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    args=parser.parse_args()
    try:
        report=run_suite(args.cases)
        write_report(report,args.output)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2,f'Invalid evaluation input or output ({type(exc).__name__}).\n')
    print(f"Automatic checks: {report['automatic_passes']}/{report['cases']} passed; {report['semantic_reviews_pending']} meaning reviews pending; 0 model calls.")
    print(f'Report: {args.output / "report.md"}')
    return int(report['automatic_failures']>0)


if __name__=='__main__':
    raise SystemExit(main())
