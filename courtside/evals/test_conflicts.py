"""Regression checks for the narrow availability-disagreement detector."""
from copy import deepcopy
import unittest

from courtside.investigation import availability_disagreements
from courtside.evals.run_news import run_suite


def material(text, published='2026-11-01T17:00:00Z', quarantined=False):
    return dict(published_at=published,quarantined=quarantined,material=dict(excerpt=text))


class AvailabilityConflictTests(unittest.TestCase):
    def detect(self,first,second):
        return availability_disagreements({'a':material(first),'b':material(second)},{'a','b'})

    def test_out_and_game_time_decision_disagree(self):
        rows=self.detect('Jordan Vale would miss tonight\'s game.', 'Jordan Vale remains a game-time decision for tonight.')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['resolution'],'unresolved')
        self.assertEqual({c['status'] for c in rows[0]['claims']},{'out','uncertain'})

    def test_agreement_does_not_raise_a_warning(self):
        self.assertEqual(self.detect('Jordan Vale is out tonight.', 'Jordan Vale will miss tonight\'s game.'),[])

    def test_different_players_or_days_are_not_conflated(self):
        self.assertEqual(self.detect('Jordan Vale is out tonight.', 'Casey North is available tonight.'),[])
        self.assertEqual(self.detect('Jordan Vale is out tonight.', 'Jordan Vale is available tomorrow.'),[])

    def test_missing_game_day_and_negation_are_conservative(self):
        self.assertEqual(self.detect('Jordan Vale is out.', 'Jordan Vale is available.'),[])
        self.assertEqual(self.detect('Jordan Vale is not out tonight.', 'Jordan Vale remains questionable tonight.'),[])

    def test_relative_day_uses_publication_date(self):
        sources={'a':material('Jordan Vale is out tomorrow.'),
                 'b':material('Jordan Vale is available tonight.','2026-11-02T17:00:00Z')}
        rows=availability_disagreements(sources,set(sources))
        self.assertEqual(rows[0]['game_date'],'2026-11-02')

    def test_quarantined_and_unrelated_material_is_excluded(self):
        sources={'a':material('Jordan Vale is out tonight.'),
                 'b':material('Jordan Vale is available tonight.',quarantined=True)}
        self.assertEqual(availability_disagreements(sources,set(sources)),[])
        sources['b']['quarantined']=False
        self.assertEqual(availability_disagreements(sources,{'a'}),[])

    def test_integration_keeps_both_accounts_and_changes_corroboration(self):
        report=run_suite()
        row=next(r for r in report['results'] if r['id']=='conflicting-availability')
        story=row['output']['candidates'][0]
        record=story['investigation']
        self.assertEqual(len(story['summary']['citations']),2)
        self.assertEqual(record['conflicts']['status'],'detected')
        self.assertEqual(record['corroboration']['status'],'conflicting_reports')
        self.assertTrue(any('Sources disagree' in text for text in record['uncertainty']))
        self.assertEqual(record['corroboration']['retrieved_reporting_groups'],2)
        agreed=next(r for r in report['results'] if r['id']=='independent-reports')
        self.assertEqual(agreed['output']['candidates'][0]['investigation']['conflicts']['status'],'not_detected')
