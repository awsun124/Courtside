"""Review saved live news locally. No collection, model calls or public endpoints."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from .run_news import grade_case
from ..database import database_setting
from ..investigation import InvestigationSettings, evidence_id, suspicious_text
from ..storage import read_briefings, read_run
from scripts.validate_gdelt import canonical_url

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / 'artifacts/evals/production'


def code_version():
    files = ['investigation.py', 'investigate.py', 'evals/production.py', 'evals/run_news.py']
    return {name: hashlib.sha256((ROOT/'courtside'/name).read_bytes()).hexdigest() for name in files}


def make_case(briefing, result, story):
    targets = {canonical_url(url) for url in story.get('article_urls', story.get('evidence_urls', []))}
    groups = {canonical_url(url): str(index) for index, group in enumerate(story.get('reporting_groups', []))
              for url in group['evidence_urls']}
    articles = []
    for source in result['articles']:
        article = deepcopy(source)
        article['id'] = evidence_id(article['url'])
        article['reporting_group'] = groups.get(canonical_url(article['url']), article['id'])
        article['investigation_allowed'] = article.get('investigation_allowed') is not False
        articles.append(article)
    return dict(id=f"live-{briefing['briefing_id']}-{story['id']}", split='development', category='production_review',
                task='Check this story against its saved sources.',
                input=dict(as_of=briefing['as_of'], articles=articles,
                           target_article_ids=[a['id'] for a in articles if canonical_url(a['url']) in targets]),
                expected=dict(summary='supported_excerpts_or_insufficient_evidence',
                              required_meaning=[], forbidden_meaning=[],
                              allowed_citation_article_ids=[a['id'] for a in articles
                                  if canonical_url(a['url']) in targets and a['investigation_allowed']],
                              quarantine_article_ids=[], retrieved_reporting_groups=None))


def evaluate_saved(briefing, result):
    settings = InvestigationSettings(**{key: value for key, value in briefing.get('investigation', {}).get('limits', {}).items()
                                       if key in InvestigationSettings.__dataclass_fields__})
    rows = []
    for story in briefing['candidates']:
        case = make_case(briefing, result, story)
        output = {**briefing, 'candidates': [story], 'stories': [story]}
        checks = grade_case(case, output, output, settings)
        # No pre-investigation snapshot or labeled expectations exists for a live run.
        for key in ('rankings_unchanged', 'expected_summary_presence', 'quarantine_expectations', 'reporting_group_count'):
            checks.pop(key, None)
        record = story.get('investigation', {})
        activity = record.get('activity', [])
        checks['story_steps_within_limit'] = len(activity) <= settings.max_steps_per_story
        checks['retrievals_within_limit'] = sum(step.get('tool') == 'retrieve_material' for step in activity) <= settings.max_articles_per_story
        retrieved = {eid for step in activity if step.get('tool') == 'retrieve_material' for eid in step.get('evidence_ids', [])}
        references = {ref['evidence_id']: ref for ref in record.get('evidence_references', [])}
        suspicious = {a['id'] for a in case['input']['articles'] if any(suspicious_text(a.get(field) or '') for field in ('title', 'description', 'content_excerpt'))}
        checks['suspicious_material_quarantined'] = all(
            eid in references and references[eid].get('quarantined') is True for eid in retrieved & suspicious)
        checks['restricted_story_has_no_summary'] = record.get('status') != 'source_restricted' or not story.get('summary')
        if record.get('conflicts', {}).get('status') == 'detected':
            checks['conflict_warning_present'] = any('Sources disagree' in text for text in record.get('uncertainty', []))
        rows.append(dict(id=case['id'], title=story['title'], checks=checks,
                         automatic_status='pass' if all(checks.values()) else 'fail',
                         conflict=record.get('conflicts', {}).get('status') == 'detected',
                         summary=story.get('summary'), investigation=record, regression_input=case,
                         review=dict(status='pending', reviewer=None, notes=None)))
    return dict(schema_version=1, briefing_id=briefing['briefing_id'], as_of=briefing['as_of'],
                data_mode=briefing.get('data_mode'), processing_run_id=briefing['processing_run_id'],
                source_sha256=hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
                limits=briefing.get('investigation', {}).get('limits'),
                counts=dict(stories=len(rows), summaries=sum(bool(row['summary']) for row in rows),
                            restricted=sum(row['investigation'].get('status') == 'source_restricted' for row in rows),
                            conflicts=sum(row['conflict'] for row in rows), manual_reviews=0),
                evaluated_at=datetime.now(timezone.utc).isoformat(), evaluator_code=code_version(),
                investigator_code=briefing.get('investigation', {}).get('code_version'),
                elapsed_seconds=briefing.get('investigation', {}).get('elapsed_seconds'), stories=rows)


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    collect = sub.add_parser('collect')
    collect.add_argument('--database', default=None)
    collect.add_argument('--limit', type=int, default=7)
    collect.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    show = sub.add_parser('show')
    show.add_argument('report', type=Path)
    show.add_argument('--story', required=True)
    review = sub.add_parser('review')
    review.add_argument('report', type=Path)
    review.add_argument('--story', required=True)
    review.add_argument('--verdict', choices=['pass', 'fail'], required=True)
    review.add_argument('--reviewer', required=True)
    review.add_argument('--notes', required=True)
    review.add_argument('--required-meaning', action='append', default=[])
    review.add_argument('--forbidden-meaning', action='append', default=[])
    review.add_argument('--summary', choices=['supported_excerpts', 'insufficient_evidence', 'supported_excerpts_or_insufficient_evidence'])
    export = sub.add_parser('export-case')
    export.add_argument('review', type=Path)
    export.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'collect':
        if not 1 <= args.limit <= 100:
            parser.error('--limit must be between 1 and 100')
        for briefing in read_briefings(args.database or database_setting(), 'live', limit=args.limit):
            path = args.output / f"briefing-{briefing['briefing_id']}.json"
            if path.exists():
                continue
            result = read_run(args.database or database_setting(), briefing['processing_run_id'])
            if result is None:
                parser.error('Saved source run is missing')
            report = evaluate_saved(briefing, result)
            write_private(path, report)
            # Failures and disagreements first, then ordinary stories. No accuracy claim.
            sample = sorted(report['stories'], key=lambda row: (row['automatic_status'] != 'fail', not row['conflict'], row['id']))[:3]
            print(f"{path}: {sum(r['automatic_status']=='fail' for r in report['stories'])} automatic failures; "
                  f"{report['counts']['summaries']} summaries, {report['counts']['restricted']} restricted stories; manual review pending")
            for row in sample:
                print(f"  Review {row['id']}: {row['title']}")
    elif args.command == 'show':
        report = json.loads(args.report.read_text())
        story = next((row for row in report['stories'] if row['id'] == args.story), None)
        if story is None:
            parser.error('Unknown story')
        print(json.dumps(story, indent=2, ensure_ascii=False))
    elif args.command == 'review':
        report = json.loads(args.report.read_text())
        story = next((row for row in report['stories'] if row['id'] == args.story), None)
        if story is None or not args.notes.strip() or not args.reviewer.strip():
            parser.error('Provide a known story, reviewer and nonempty notes')
        entry = dict(story=story, report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
                     reviewed_at=datetime.now(timezone.utc).isoformat(), reviewer=args.reviewer,
                     verdict=args.verdict, notes=args.notes, required_meaning=args.required_meaning,
                     forbidden_meaning=args.forbidden_meaning, summary=args.summary)
        path = args.report.parent/'reviews'/f"{args.story}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.json"
        write_private(path, entry)
        print(path)
    else:
        entry = json.loads(args.review.read_text())
        if entry['verdict'] != 'fail' or not entry['required_meaning'] or not entry['forbidden_meaning'] or not entry['summary']:
            parser.error('Export needs a failed review with summary expectation and required/forbidden meaning')
        case = deepcopy(entry['story']['regression_input'])
        case['expected'].update(required_meaning=entry['required_meaning'], forbidden_meaning=entry['forbidden_meaning'], summary=entry['summary'])
        write_private(args.output, dict(schema_version=1, name='Reviewed production regression',
                      provenance=dict(kind='saved_public_reporting', reviewer=entry['reviewer'], notes=entry['notes']), cases=[case]))
        print(f"Created {args.output}. Review source permissions before publishing; this is development data.")


if __name__ == '__main__':
    main()
