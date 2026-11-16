from copy import deepcopy
import unittest

from .injury_calibration import apply_weight
from .injury_study import probability


class CalibrationTests(unittest.TestCase):
    def test_frozen_weight_reproduces_study_probability(self):
        forecasts=dict(predictions=[dict(home_probability=.7,availability=dict(status='applied',
            base_home_probability=.75,estimated_home_margin_change=-3))])
        original=deepcopy(forecasts)
        for weight in (0,.25,.5,1,2):
            calibrated=apply_weight(forecasts,weight)['predictions'][0]
            study=dict(base_probability=.75,injury_logit_shift=-.3)
            self.assertAlmostEqual(calibrated['home_probability'],probability(study,weight))
        self.assertEqual(original,forecasts)

    def test_missing_report_leaves_base_intact(self):
        forecasts=dict(predictions=[dict(home_probability=.65,availability=dict(status='unavailable',base_home_probability=.65))])
        result=apply_weight(forecasts,.25)['predictions'][0]
        self.assertAlmostEqual(result['home_probability'],.65)
        self.assertEqual(result['availability']['status'],'unavailable')

    def test_invalid_weights_rejected(self):
        for value in (-1,3,float('nan'),True):
            with self.assertRaises(ValueError):apply_weight(dict(predictions=[]),value)
