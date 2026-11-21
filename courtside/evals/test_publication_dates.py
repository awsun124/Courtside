"""Incomplete dates must not prevent an evidence-based investigation."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from courtside.evals.run_news import adapt_case, evaluate_case, load_cases
from courtside.investigation import InvestigationSettings, publication_time


class PublicationDateTests(unittest.TestCase):
    def case(self):
        suite=load_cases(Path(__file__).with_name('news_cases_hard.json'))
        return deepcopy(next(c for c in suite['cases'] if c['id']=='missing-publication-time'))

    def evaluate(self,case):
        settings=InvestigationSettings()
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'rules.json'
            config.write_text(json.dumps(asdict(settings)))
            return evaluate_case(case,config,settings)

    def test_invalid_and_missing_timestamps_are_not_guessed(self):
        for value in (None,'','unknown','2026-11-05', '2026-11-05T18:00:00',123):
            with self.subTest(value=value):self.assertIsNone(publication_time(value))
        self.assertIsNotNone(publication_time('2026-11-05T18:00:00Z'))

    def test_bad_dates_keep_both_accounts_and_explain_unknown_game_day(self):
        for value in (None,'','not-a-date','2026-11-05T18:00:00',123):
            with self.subTest(value=value):
                case=self.case();case['input']['articles'][0]['published_at']=value
                evaluated=self.evaluate(case)
                self.assertEqual(evaluated['automatic_status'],'pass')
                story=evaluated['output']['candidates'][0]
                self.assertEqual(len(story['summary']['citations']),2)
                self.assertIsNone(story['summary']['citations'][0]['published_at'])
                record=story['investigation']
                self.assertEqual(record['publication_dates']['status'],'incomplete')
                self.assertEqual(record['conflicts']['date_coverage'],'incomplete')
                self.assertEqual(record['corroboration']['status'],'timing_unverified')
                self.assertTrue(any('cannot be tied to a game date' in note for note in record['uncertainty']))

    def test_missing_date_key_also_keeps_investigation_running(self):
        case=self.case();del case['input']['articles'][0]['published_at']
        self.assertEqual(self.evaluate(case)['automatic_status'],'pass')

    def test_restricted_source_filter_does_not_crash_on_allowed_undated_sources(self):
        case=self.case()
        blocked=deepcopy(case['input']['articles'][0])
        blocked.update(id='restricted',url='https://restricted.example.test/report',investigation_allowed=False)
        case['input']['articles'].append(blocked)
        case['input']['target_article_ids'].append('restricted')
        evaluated=self.evaluate(case)
        self.assertEqual(evaluated['automatic_status'],'pass')
        self.assertEqual(len(evaluated['output']['candidates'][0]['summary']['citations']),2)
