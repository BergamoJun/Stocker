"""P5.4 composition. Original financial engines and old --ai behavior stay intact."""
from . import core,research_pipeline

def build(ticker,as_of=None,*,usage_mode='research',ai=False,ai_dry_run=False,saved=None,record_origin='RUN',**kwargs):
    if kwargs.pop('ai_record',None) is not None:raise ValueError('OLD_AI_RECORD_NOT_QWEN35_IDENTITY')
    value,snapshot=core.build(ticker,as_of,ai=False,ai_dry_run=False,**kwargs)
    research_pipeline.enrich(value,mode=usage_mode,ai=ai,dry_run=ai_dry_run,saved=saved,record_origin=record_origin)
    core.validate(value);return value,snapshot

def rebuild(value,snapshot):
    p=value['research_pipeline']
    return build(value['ticker'],value['analysis_as_of'],snapshot=snapshot,illustrative=value['is_illustrative'],market_snapshot=value['market_snapshot'],
       sec_acquisition=value.get('sec_acquisition'),scenario_horizon=value.get('scenario_horizon',1),scenario_assumptions=value.get('scenario_override_inputs'),
       peer_inputs=value.get('peer_inputs',[]),usage_mode=p['usage_mode'],ai=p['ai_requested'],ai_dry_run=p['dry_run'],saved=p['record'],record_origin=p['record_origin'])[0]
