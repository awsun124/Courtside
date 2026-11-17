"""Portable, data-only version of the frozen linear ensemble; no pickle loading."""

import gzip
import hashlib
import json
from pathlib import Path

ASSETS=Path(__file__).with_name('assets')


class LinearMargin:
    def __init__(self,parameters):
        import numpy as np
        self.coefficients=np.asarray(parameters['coefficients'])
        self.intercept=parameters['intercept']

    def predict(self,matrix):
        return matrix@self.coefficients+self.intercept


def load_model(folder=ASSETS):
    raw=(folder/'model.json').read_bytes();metadata=json.loads(raw)
    def component(spec,state):
        if spec['kind']=='blend':return [component(s,t) for s,t in zip(spec['members'],state)]
        if spec['kind']=='ridge':return LinearMargin(state)
        if spec['kind'] in ('normal','baseline'):return None
        raise ValueError('Unsupported production model component')
    history_raw=(folder/'history.json.gz').read_bytes()
    if hashlib.sha256(history_raw).hexdigest()!=metadata['history_sha256']:
        raise ValueError('Production history checksum mismatch')
    history=json.loads(gzip.decompress(history_raw))
    return metadata,component(metadata['selected']['spec'],metadata['components']),history
