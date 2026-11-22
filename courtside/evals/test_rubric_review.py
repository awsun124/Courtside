"""Accept valid alternatives and reject unsafe output before freezing the rubric."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from courtside.evals.run_news import adapt_case,grade_case,load_cases,DEFAULT_CASES,run_suite
from courtside.evals.rubrics import FREEZE,verify_rubrics
from courtside.investigation import InvestigationSettings


class RubricReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases=load_cases(DEFAULT_CASES)['cases'];cls.report=run_suite()

    def fixture(self,key):
        case=next(c for c in self.cases if c['id']==key)
        _,before=adapt_case(case)
        output=deepcopy(next(r['output'] for r in self.report['results'] if r['id']==key))
        return case,before,output

    def grade(self,case,before,output):return grade_case(case,before,output,InvestigationSettings())

    def test_a_shorter_supported_quote_is_valid(self):
        case,before,output=self.fixture('completed-game');summary=output['candidates'][0]['summary']
        citation=summary['citations'][0];citation['quote']=citation['quote'].split('. ')[0]+'.'
        summary['text']=f"{citation['publisher']} reports: {citation['quote']}"
        self.assertTrue(all(self.grade(case,before,output).values()))

    def test_safe_abstention_without_reading_malicious_material_is_valid(self):
        case,before,output=self.fixture('prompt-injection-only');record=output['candidates'][0]['investigation']
        record['activity']=[step for step in record['activity'] if step['tool']!='retrieve_material']
        record['evidence_references']=[]
        self.assertTrue(all(self.grade(case,before,output).values()))

    def test_partial_retrieval_uses_the_actual_group_count(self):
        case,before,output=self.fixture('independent-reports');story=output['candidates'][0];summary=story['summary'];record=story['investigation']
        retained=summary['citations'][0];removed=summary['citations'][1]['evidence_id']
        summary['citations']=[retained];summary['text']=summary['text'].splitlines()[0]
        record['activity']=[step for step in record['activity'] if removed not in step.get('evidence_ids',[])]
        record['evidence_references']=[ref for ref in record['evidence_references'] if ref['evidence_id']!=removed]
        record['corroboration']['retrieved_reporting_groups']=1
        self.assertTrue(self.grade(case,before,output)['reporting_group_count'])
        record['corroboration']['retrieved_reporting_groups']=2
        self.assertFalse(self.grade(case,before,output)['reporting_group_count'])

    def test_an_invented_publication_date_fails(self):
        case,before,output=self.fixture('confirmed-signing')
        output['candidates'][0]['summary']['citations'][0]['published_at']='2030-01-01T00:00:00Z'
        self.assertFalse(self.grade(case,before,output)['citation_dates_match_sources'])

    def test_equivalent_timestamp_formats_are_valid(self):
        case,before,output=self.fixture('confirmed-signing')
        citation=output['candidates'][0]['summary']['citations'][0]
        citation['published_at']=citation['published_at'].replace('+00:00','Z')
        self.assertTrue(self.grade(case,before,output)['citation_dates_match_sources'])

    def test_freeze_matches_reviewed_development_cases(self):
        self.assertEqual(verify_rubrics(DEFAULT_CASES)['status'],'matches_reviewed_rubrics')

    def test_changed_expected_answers_are_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest=json.loads(FREEZE.read_text());manifest['datasets'][DEFAULT_CASES.name]['sha256']='changed'
            path=Path(folder)/'freeze.json';path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'expectations changed'):verify_rubrics(DEFAULT_CASES,path)

    def test_changed_grader_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest=json.loads(FREEZE.read_text());manifest['grading_code_sha256']['courtside/evals/run_news.py']='changed'
            path=Path(folder)/'freeze.json';path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'Grading code changed'):verify_rubrics(DEFAULT_CASES,path)
