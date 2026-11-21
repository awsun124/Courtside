import unittest
from pathlib import Path
from unittest.mock import patch
from courtside.evals.run_reserved import prepare_cases,verify_freeze
from courtside.evals.run_news import load_cases,DEFAULT_CASES


class ReservedRunnerTests(unittest.TestCase):
    def test_validation_preserves_reserved_labels_without_running_cases(self):
        from copy import deepcopy
        case=deepcopy(load_cases(DEFAULT_CASES)['cases'][0]);case['split']='reserved'
        suite={'schema_version':1,'cases':[case]}
        with patch('courtside.evals.run_reserved.evaluate_case',side_effect=AssertionError('Do not evaluate')) as evaluate:
            self.assertEqual(prepare_cases(suite)[0]['split'],'reserved')
            self.assertEqual(suite['cases'][0]['split'],'reserved')
            evaluate.assert_not_called()

    def test_development_input_is_not_accepted_as_reserved(self):
        with self.assertRaises(ValueError):prepare_cases(load_cases(DEFAULT_CASES))

    def test_changed_file_prevents_frozen_run(self):
        from subprocess import CompletedProcess
        with patch('courtside.evals.run_reserved.subprocess.run',side_effect=[CompletedProcess([],0,'abc\n'),CompletedProcess([],0,b'frozen')]),patch.object(Path,'read_bytes',return_value=b'changed'):
            with self.assertRaisesRegex(ValueError,'does not match'):verify_freeze('abc')
