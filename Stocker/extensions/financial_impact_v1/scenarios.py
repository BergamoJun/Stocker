"""Mutually exclusive financial scenarios, never additive stock forecasts."""
from copy import deepcopy
from .report import analyze


def standard(packet,*,created_at=None):
    results=[]
    for label,shock in [('Base','0'),('Bull','-25'),('Bear','25')]:
        value=deepcopy(packet)
        value['assumptions']={**value['assumptions'],'scenario_id':packet['assumptions']['scenario_id']+'.'+label,
            'label':label,'rate_delta_override':{'value':shock,'unit':'bp'},
            'description':label+' financial expense scenario; alternative rate shock '+shock+'bp; not a stock direction',
            'probability':None}
        results.append({'input':value,'output':analyze(value,created_at=created_at)})
    return results
