"""Apply a frozen validation-selected injury weight without fitting at inference."""

from copy import deepcopy
import json
import math

from .improve import digest

# Selected on January 2026 validation, before evaluating February-April.
# Keep the runtime default independent of ignored local research artifacts.
DEFAULT_WEIGHT = 0.25


def load_weight(folder):
    path=folder/'selection.json'
    selection=json.loads(path.read_text())
    weight=selection['weight']
    if type(weight) not in (int,float) or not math.isfinite(weight) or not 0<=weight<=2:
        raise ValueError('Invalid frozen injury weight')
    for name,expected in selection['code_sha256'].items():
        from pathlib import Path
        if digest(Path(__file__).with_name(name))!=expected:
            raise ValueError('Injury evaluation implementation changed')
    for name,key in [('sources.json','sources_sha256'),('validation-adjustments.json','validation_sha256'),
                     ('reconciled-player-history.json','player_history_sha256')]:
        if digest(folder/name)!=selection[key]:raise ValueError('Frozen injury calibration input changed')
    return weight,digest(path)


def apply_weight(forecasts,weight):
    if type(weight) not in (int,float) or not math.isfinite(weight) or not 0<=weight<=2:
        raise ValueError('Invalid injury calibration weight')
    result=deepcopy(forecasts)
    for row in result['predictions']:
        note=row.get('availability')
        if not note:continue
        base=note['base_home_probability']
        shift=.10*note.get('estimated_home_margin_change',0)*weight
        adjusted=1/(1+math.exp(-(math.log(base/(1-base))+shift)))
        row['home_probability']=adjusted
        note.update(calibration_weight=weight,home_probability_change=adjusted-base)
        if 'estimated_home_margin_change' in note:
            note['weighted_home_margin_change']=note['estimated_home_margin_change']*weight
        if weight==0 and note['status']=='applied':
            note.update(status='reviewed',reason='Official report reviewed; validation selected no probability adjustment')
    return result
