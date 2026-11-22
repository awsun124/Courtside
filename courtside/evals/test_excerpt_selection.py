"""Keep summaries readable without changing the evidence."""
from copy import deepcopy
from pathlib import Path
import unittest

from courtside.evals.run_news import adapt_case,grade_case,load_cases,run_suite
from courtside.investigation import InvestigationSettings,choose_source_excerpt,supported_summary


def material(publisher,quote):
    return dict(publisher=publisher,url=f'https://{publisher}.example.test/report',published_at='2026-11-21T12:00:00Z',
                quarantined=False,material={'description':quote})


class ExcerptSelectionTests(unittest.TestCase):
    def test_repeated_quotes_display_once_but_keep_both_links(self):
        quote='The Lakers beat the Kings 106-104.'
        retrieved={'a':material('First',quote),'b':material('Second',quote)}
        summary=supported_summary([{'evidence_id':key,'quote':quote} for key in retrieved],retrieved,set(retrieved))
        self.assertEqual(len(summary['text'].splitlines()),1)
        self.assertEqual(len(summary['citations']),2)
        self.assertEqual({c['url'] for c in summary['citations']},{m['url'] for m in retrieved.values()})

    def test_different_scores_or_uncertainty_are_not_combined(self):
        for first,second in [('The Lakers won 106-104.','The Lakers won 106-105.'),
                             ('Jordan Vale is out tonight.','Jordan Vale is questionable tonight.')]:
            with self.subTest(second=second):
                retrieved={'a':material('First',first),'b':material('Second',second)}
                summary=supported_summary([dict(evidence_id='a',quote=first),dict(evidence_id='b',quote=second)],retrieved,set(retrieved))
                self.assertEqual(len(summary['text'].splitlines()),2)

    def test_every_duplicate_citation_still_needs_valid_evidence(self):
        quote='The Lakers beat the Kings 106-104.'
        retrieved={'a':material('First',quote),'b':material('Second','Unrelated reporting.')}
        with self.assertRaises(ValueError):
            supported_summary([dict(evidence_id='a',quote=quote),dict(evidence_id='b',quote=quote)],retrieved,set(retrieved))

    def test_complete_saved_sentence_is_preferred_to_cut_off_description(self):
        fields={'description':'The Raptors beat the Pistons with a late',
                'excerpt':'The Raptors beat the Pistons 105-102. Further reporting was cut',
                'title':'Raptors win'}
        self.assertEqual(choose_source_excerpt(fields),'The Raptors beat the Pistons 105-102.')

    def test_no_complete_sentence_keeps_original_fragment_with_ellipsis(self):
        fields={'description':'The Raptors beat the Pistons with a late'}
        quote=choose_source_excerpt(fields)
        retrieved={'a':material('First',quote)}
        summary=supported_summary([dict(evidence_id='a',quote=quote)],retrieved,{'a'})
        self.assertEqual(summary['citations'][0]['quote'],fields['description'])
        self.assertTrue(summary['text'].endswith('late…'))
        self.assertNotIn('basket',summary['text'])

    def test_saved_reporting_deduplication_passes_grader_without_losing_citations(self):
        path=Path(__file__).with_name('news_cases_real.json')
        suite=load_cases(path);report=run_suite(path)
        row=next(r for r in report['results'] if r['id']=='real-lakers-kings-final')
        summary=row['output']['candidates'][0]['summary']
        self.assertEqual(len(summary['text'].splitlines()),1)
        self.assertEqual(len(summary['citations']),2)
        self.assertEqual(row['automatic_status'],'pass')
        case=next(c for c in suite['cases'] if c['id']==row['id']);_,before=adapt_case(case)
        tampered=deepcopy(row['output'])
        tampered['candidates'][0]['summary']['citations'][1]['url']='https://wrong.example.test/report'
        self.assertFalse(grade_case(case,before,tampered,InvestigationSettings())['citations_retrieved_permitted_and_supported'])
