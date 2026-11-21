"""Availability claims must agree on player, game date and correction history."""
import unittest
from pathlib import Path

from courtside.evals.run_news import run_suite
from courtside.investigation import availability_assessment,availability_disagreements
from courtside.evals.test_conflicts import material


def sources(second,first='Jordan Vale will not play tonight.',same_group=True):
    data={'a':material(first,'2026-11-05T14:00:00Z'),
          'b':material(second,'2026-11-05T21:00:00Z')}
    for key,row in data.items():row['url']=f'https://{key}.example.test/report'
    groups=[{'evidence_urls':[row['url'] for row in data.values()]}] if same_group else []
    # The production assessment identifies grouping by URL-derived evidence IDs.
    from courtside.investigation import evidence_id
    data={evidence_id(row['url']):row for row in data.values()}
    return data,groups


class AvailabilityUpdateTests(unittest.TestCase):
    def test_indirect_absence_and_explicit_dates(self):
        for phrase in ('tonight','November 5','Nov. 5','2026-11-05','November 5, 2026'):
            with self.subTest(phrase=phrase):
                data={'a':material(f'Jordan Vale will not play {phrase}.','2026-11-05T14:00:00Z'),
                      'b':material(f'Jordan Vale is available {phrase}.','2026-11-05T21:00:00Z')}
                rows=availability_disagreements(data,set(data))
                self.assertEqual(rows[0]['game_date'],'2026-11-05')

    def test_different_years_and_ambiguous_dates_do_not_match(self):
        data={'a':material('Jordan Vale is out November 5, 2026.'),
              'b':material('Jordan Vale is available November 5, 2027.')}
        self.assertEqual(availability_disagreements(data,set(data)),[])
        data['b']=material('Jordan Vale is available tonight or tomorrow.')
        self.assertEqual(availability_disagreements(data,set(data)),[])

    def test_later_same_group_correction_replaces_old_claim(self):
        data,groups=sources('Correction to our earlier report: Jordan Vale is available tonight.')
        assessment=availability_assessment(data,set(data),groups)
        self.assertEqual(assessment['disagreements'],[])
        self.assertEqual(len(assessment['corrections']),1)
        self.assertEqual(assessment['corrections'][0]['corrected_status'],'available')

    def test_independent_or_merely_newer_reports_do_not_resolve_conflicts(self):
        for text,same_group in [('Correction to our earlier report: Jordan Vale is available tonight.',False),
                                ('Jordan Vale is available tonight.',True)]:
            data,groups=sources(text,same_group=same_group)
            assessment=availability_assessment(data,set(data),groups)
            self.assertTrue(assessment['disagreements'])
            self.assertEqual(assessment['corrections'],[])

    def test_older_correction_cannot_override_a_newer_report(self):
        data,groups=sources('Correction to our earlier report: Jordan Vale is available tonight.')
        latest=list(data.values())[1];latest['published_at']='2026-11-05T13:00:00Z'
        assessment=availability_assessment(data,set(data),groups)
        self.assertTrue(assessment['disagreements'])
        self.assertEqual(assessment['corrections'],[])

    def test_expected_to_play_does_not_mean_confirmed_available(self):
        data={'a':material('Jordan Vale is expected to play tonight.'),
              'b':material('Jordan Vale is available tonight.')}
        rows=availability_disagreements(data,set(data))
        self.assertEqual({c['status'] for c in rows[0]['claims']},{'uncertain','available'})

    def test_harder_case_summary_uses_correction_and_retains_evidence_history(self):
        report=run_suite(Path(__file__).with_name('news_cases_hard.json'))
        row=next(r for r in report['results'] if r['id']=='explicit-later-correction')
        self.assertEqual(row['automatic_status'],'pass')
        self.assertIn('Correction',row['output']['candidates'][0]['title'])
        story=row['output']['candidates'][0];record=story['investigation']
        correction=record['corrections'][0]
        self.assertEqual(story['summary']['citations'][0]['evidence_id'],correction['correction_evidence_id'])
        self.assertNotIn(correction['superseded_evidence_id'],[c['evidence_id'] for c in story['summary']['citations']])
        self.assertIn(correction['superseded_evidence_id'],[e['evidence_id'] for e in record['evidence_references']])
        self.assertTrue(any('source corrected' in note for note in record['uncertainty']))
