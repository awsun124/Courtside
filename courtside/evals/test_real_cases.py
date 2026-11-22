"""Saved reporting tests preserve source restrictions and avoid invented facts."""
from pathlib import Path
import unittest

from courtside.evals.run_news import load_cases,run_suite

CASES=Path(__file__).with_name('news_cases_real.json')


class SavedReportingTests(unittest.TestCase):
    def test_real_cases_keep_permissions_and_short_saved_snippets(self):
        suite=load_cases(CASES)
        self.assertEqual(suite['provenance']['kind'],'saved_public_reporting')
        self.assertEqual(len(suite['cases']),6)
        restricted=0
        for case in suite['cases']:
            permitted=[]
            for article in case['input']['articles']:
                self.assertIsNone(article['content_excerpt'])
                self.assertLessEqual(len((article.get('description') or '').split()),25)
                if article['investigation_allowed']:permitted.append(article['id'])
                else:self.assertIsNone(article['description'])
            self.assertEqual(case['expected']['allowed_citation_article_ids'],permitted)
            restricted+=not permitted
        self.assertEqual(restricted,4)

    def test_saved_reporting_runs_offline_and_is_not_labeled_fictional(self):
        report=run_suite(CASES)
        self.assertEqual(report['automatic_passes'],6)
        self.assertNotIn('Fictional',report['scope'])
        self.assertEqual(report['semantic_reviews_pending'],6)
        for row in report['results'][:4]:
            story=row['output']['candidates'][0]
            self.assertIsNone(story['summary'])
            self.assertEqual(story['investigation']['status'],'source_restricted')
