"""Keep reserved cases separate from development runs."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from courtside.evals.run_news import load_cases, run_suite

FOLDER=Path(__file__).parent


class CaseSplitTests(unittest.TestCase):
    def test_split_files_match_manifest_and_have_unique_case_ids(self):
        manifest=json.loads((FOLDER/'case_splits.json').read_text())
        ids=set()
        for name,expected in manifest['files'].items():
            raw=(FOLDER/name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),expected['sha256'])
            cases=json.loads(raw)['cases']
            self.assertEqual(len(cases),expected['cases'])
            current={c['id'] for c in cases}
            self.assertEqual(len(current),len(cases))
            self.assertFalse(ids & current)
            ids.update(current)
            self.assertTrue(all(c['split']==('reserved' if 'reserved' in name else 'development') for c in cases))

    def test_development_loader_accepts_harder_cases(self):
        self.assertEqual(len(load_cases(FOLDER/'news_cases_hard.json')['cases']),12)

    def test_runner_refuses_reserved_cases_before_investigation(self):
        with patch('courtside.evals.run_news.evaluate_case',side_effect=AssertionError('Reserved cases must not run')) as evaluate:
            with self.assertRaisesRegex(ValueError,'development cases only'):
                run_suite(FOLDER/'news_cases_reserved.json')
            evaluate.assert_not_called()
