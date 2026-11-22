"""Exercise model comparison with a fake provider, never paid API calls."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from courtside.evals.compare_model import Budget,compare,write_comparison
from courtside.investigation import RulesProvider


class FakeModel(RulesProvider):
    name='fake-model'
    uses_llm=True
    def __init__(self,settings,env_file,budget):
        self.settings=settings;self.budget=budget;self.calls_made=0
        self.token_usage=dict(prompt_tokens=0,completion_tokens=0,reported_calls=0)
    def choose(self,state,timeout):
        self.budget.reserve({'max_completion_tokens':self.settings.max_output_tokens,'messages':'fake'})
        self.calls_made+=1
        self.token_usage['prompt_tokens']+=100
        self.token_usage['completion_tokens']+=10
        self.token_usage['reported_calls']+=1
        return super().choose(state,timeout)


class ModelComparisonTests(unittest.TestCase):
    def execute(self,**kwargs):
        return compare(execute=True,max_usd='1',input_rate='0.1',output_rate='0.2',provider_factory=FakeModel,**kwargs)

    def test_default_is_dry_run_without_credentials_or_network(self):
        with patch('courtside.llm.read_provider_settings',side_effect=AssertionError('No credentials')),patch('subprocess.run',side_effect=AssertionError('No network')):
            report=compare()
        self.assertEqual(report['mode'],'dry_run')
        self.assertEqual(report['results'],[])

    def test_execute_requires_explicit_prices_and_budget(self):
        with self.assertRaises(ValueError):compare(execute=True)
        for amount in ('NaN','Infinity','-1'):
            with self.subTest(amount=amount),self.assertRaises(ValueError):compare(max_usd=amount)

    def test_fake_comparison_records_usage_traces_and_pending_review(self):
        with patch('subprocess.run',side_effect=AssertionError('No real requests')):
            report=self.execute(max_cases=1)
        self.assertEqual(report['comparison']['completed_pairs'],1)
        row=report['results'][0]
        self.assertEqual(row['rules']['automatic_status'],'pass')
        self.assertEqual(row['model']['automatic_status'],'pass')
        self.assertEqual(row['model']['review']['status'],'pending')
        self.assertTrue(row['model']['usage_complete'])
        self.assertGreater(row['model']['token_usage']['prompt_tokens'],0)
        self.assertTrue(row['model']['output']['candidates'][0]['investigation']['activity'])

    def test_request_cap_is_shared_across_cases(self):
        report=self.execute(max_cases=3,max_requests=1)
        self.assertEqual(report['budget']['requests_reserved'],1)
        self.assertTrue(report['budget']['stopped'])
        self.assertEqual(report['results'][0]['model']['status'],'budget_exhausted')
        self.assertEqual(report['results'][1]['model']['status'],'not_run_budget')
        self.assertEqual(report['comparison']['completed_pairs'],0)

    def test_cost_limit_stops_before_the_first_request(self):
        report=compare(execute=True,max_cases=1,max_usd='0.000001',input_rate='1',output_rate='1',provider_factory=FakeModel)
        self.assertEqual(report['budget']['requests_reserved'],0)
        self.assertTrue(report['budget']['stopped'])

    def test_failed_request_reservations_are_not_refunded(self):
        class FailedModel(FakeModel):
            def choose(self,state,timeout):
                self.budget.reserve({'max_completion_tokens':512});self.calls_made+=1
                raise ValueError('fake failure')
        report=compare(execute=True,max_cases=1,max_usd='1',input_rate='1',output_rate='1',provider_factory=FailedModel)
        self.assertEqual(report['budget']['requests_reserved'],1)
        self.assertEqual(report['results'][0]['model']['automatic_status'],'fail')
        self.assertFalse(report['results'][0]['model']['usage_complete'])
        self.assertIsNone(report['results'][0]['model']['reported_token_cost_usd'])

    def test_output_token_cap_stops_before_request(self):
        budget=Budget(10,512,Decimal('1'),Decimal('1'),Decimal('1'))
        budget.reserve({'max_completion_tokens':512})
        with self.assertRaises(ValueError):budget.reserve({'max_completion_tokens':512})
        self.assertEqual(budget.requests,1)

    def test_missing_configuration_does_not_count_as_a_completed_model_run(self):
        def unavailable(*args):raise ValueError('not configured')
        report=compare(execute=True,max_cases=1,max_usd='1',input_rate='1',output_rate='1',provider_factory=unavailable)
        self.assertEqual(report['results'][0]['model']['status'],'not_run_configuration')
        self.assertEqual(report['comparison']['completed_pairs'],0)

    def test_reserved_set_is_not_accepted(self):
        with self.assertRaises(ValueError):compare(Path(__file__).with_name('news_cases_reserved.json'))

    def test_dry_run_report_does_not_claim_model_results(self):
        with tempfile.TemporaryDirectory() as folder:
            write_comparison(compare(),Path(folder))
            text=(Path(folder)/'report.md').read_text()
            self.assertIn('dry_run',text)
            self.assertIn('no requests made',text)

    def test_restricted_sources_are_not_counted_as_model_evaluations(self):
        path=Path(__file__).with_name('news_cases_real.json')
        with patch('courtside.llm.read_provider_settings',side_effect=AssertionError('No provider needed')):
            report=self.execute(cases_path=path,max_cases=4)
        self.assertEqual(report['budget']['requests_reserved'],0)
        self.assertEqual(report['comparison']['completed_pairs'],0)
        self.assertTrue(all(row['model']['status']=='not_run_source_restricted' for row in report['results']))

    def test_real_client_hook_blocks_second_request_and_records_usage(self):
        from subprocess import CompletedProcess
        import json
        from courtside.evals.compare_model import EvaluationProvider
        from courtside.investigation import InvestigationSettings
        response={'usage':{'prompt_tokens':7,'completion_tokens':2},'choices':[{'message':{'tool_calls':[{'id':'call_test','function':{'name':'finish','arguments':'{"claims":[]}'}}]}}]}
        with tempfile.TemporaryDirectory() as folder:
            env=Path(folder)/'.env';env.write_text('ENABLE_LLM_CALLS=true\nLLM_API_KEY=fake-private-key\nLLM_MODEL=test-model\nLLM_BASE_URL=https://provider.example/v1\n')
            budget=Budget(1,1024,Decimal('1'),Decimal('1'),Decimal('1'))
            provider=EvaluationProvider(InvestigationSettings(max_output_tokens=512),env,budget)
            provider.start({'story_id':'test'})
            with patch('subprocess.run',return_value=CompletedProcess([],0,json.dumps(response)+'\n200','')) as transport:
                provider.choose({'steps_left':1,'calls_left':1},1)
                with self.assertRaises(ValueError):provider.choose({'steps_left':1,'calls_left':1},1)
                self.assertEqual(transport.call_count,1)
            self.assertEqual(provider.token_usage,{'prompt_tokens':7,'completion_tokens':2,'reported_calls':1})
