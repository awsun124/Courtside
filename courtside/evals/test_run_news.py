from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from courtside.evals.run_news import DEFAULT_CASES, adapt_case, grade_case, load_cases, run_suite, write_report
from courtside.investigation import InvestigationSettings


class NewsEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite=load_cases(DEFAULT_CASES)
        cls.report=run_suite()

    def fixture(self, key='confirmed-signing'):
        case=next(c for c in self.suite['cases'] if c['id']==key)
        _,before=adapt_case(case)
        output=deepcopy(next(r['output'] for r in self.report['results'] if r['id']==key))
        return case,before,output

    def grade(self,case,before,output):
        return grade_case(case,before,output,InvestigationSettings())

    def test_offline_baseline_keeps_semantic_review_pending(self):
        self.assertEqual(self.report['automatic_failures'],0)
        self.assertEqual(self.report['llm_calls'],0)
        self.assertEqual(self.report['semantic_reviews_pending'],len(self.suite['cases']))
        self.assertTrue(all(r['review']['status']=='pending' for r in self.report['results']))

    def test_adapter_never_supplies_expected_answers(self):
        case=deepcopy(self.suite['cases'][0])
        before=adapt_case(case)
        case['expected']={'secret_answer':'DO NOT PASS TO INVESTIGATOR'}
        case['task']='DO NOT PASS THE REVIEW QUESTION AS SOURCE EVIDENCE'
        self.assertEqual(adapt_case(case),before)

    def test_citation_url_alone_does_not_validate_fabricated_claim(self):
        case,before,output=self.fixture()
        output['candidates'][0]['summary']['citations'][0]['quote']='Jordan Vale signed a $20 million deal.'
        checks=self.grade(case,before,output)
        self.assertFalse(checks['citations_retrieved_permitted_and_supported'])
        self.assertFalse(checks['display_matches_cited_excerpts'])

    def test_summary_cannot_smuggle_uncited_display_text(self):
        case,before,output=self.fixture()
        output['candidates'][0]['summary']['text']+=' A trade was completed.'
        self.assertFalse(self.grade(case,before,output)['display_matches_cited_excerpts'])

    def test_abstaining_on_supported_news_is_not_a_pass(self):
        case,before,output=self.fixture()
        output['candidates'][0]['summary']=None
        self.assertFalse(self.grade(case,before,output)['expected_summary_presence'])

    def test_missing_retrieval_and_rank_mutation_are_detected(self):
        case,before,output=self.fixture()
        story=output['candidates'][0]
        story['score']=99
        story['investigation']['activity']=[s for s in story['investigation']['activity'] if s['tool']!='retrieve_material']
        checks=self.grade(case,before,output)
        self.assertFalse(checks['rankings_unchanged'])
        self.assertFalse(checks['citations_retrieved_permitted_and_supported'])

    def test_disabling_quarantine_is_detected(self):
        case,before,output=self.fixture('prompt-injection-only')
        output['candidates'][0]['investigation']['evidence_references'][0]['quarantined']=False
        self.assertFalse(self.grade(case,before,output)['quarantine_expectations'])

    def test_duplicate_case_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cases.json'
            suite=deepcopy(self.suite)
            suite['cases'].append(suite['cases'][0])
            path.write_text(json.dumps(suite))
            with self.assertRaises(ValueError):load_cases(path)

    def test_report_contains_review_rubrics_and_tool_activity(self):
        with tempfile.TemporaryDirectory() as folder:
            write_report(self.report,Path(folder))
            saved=json.loads((Path(folder)/'results.json').read_text())
            self.assertTrue(saved['results'][0]['review']['forbidden_meaning'])
            self.assertTrue(saved['results'][0]['output']['candidates'][0]['investigation']['activity'])
            self.assertIn('not whether each answer means the right thing',(Path(folder)/'report.md').read_text())


if __name__=='__main__':unittest.main()
