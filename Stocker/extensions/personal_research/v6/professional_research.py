"""P6 composes the unchanged deterministic engine with optional cloud analysis."""
from copy import deepcopy
from . import core,professional_packet as packet_api,professional_schema as contract,openai_provider as provider


def enrich(value,*,mode='research',ai=False,dry_run=False,config=None,saved=None,record_origin='RUN',**permissions):
    if mode not in {'research','verified'}:raise ValueError('OPENAI_MODE_MUST_BE_RESEARCH_OR_VERIFIED')
    if record_origin not in {'RUN','SAVED_REPLAY'}:raise ValueError('INVALID_P6_RECORD_ORIGIN')
    config=config or provider.configuration();packet=packet_api.build(value)
    if saved is None:record=provider.execute(packet,config,enabled=ai,dry_run=dry_run,**permissions)
    else:record=provider.validate_saved(saved,packet,config)
    assessment=contract.assess(record['response'],packet) if record['status']=='COMPLETED' else {
        'status':'DETERMINISTIC_FALLBACK','schema_status':'NOT_RUN','rows':[],'drafts':[],'monitoring':[],
        'unresolved':[],'verified_claims':[],'independent_semantic_accuracy':'NOT_MEASURED','economic_usefulness':'NOT_MEASURED'}
    mock=record['origin']=='ILLUSTRATIVE_MOCK_NOT_LIVE'
    state=assessment['status']
    value['professional_research']={'version':'professional-research-v1','provider':'openai_gpt61_sol_v1',
        'model':config['model'],'mode':mode,'ai_requested':ai,'dry_run':dry_run,'config':deepcopy(config),
        'record_origin':record_origin,'record':record,'packet':packet,'validation':assessment,
        'research_state':('ILLUSTRATIVE_MOCK_NOT_LIVE' if mock and assessment['drafts'] else state),
        'verified_state':'DETERMINISTIC_FALLBACK','verified_claims':0,
        'api_requests_this_run':record['request_count'] if record_origin=='RUN' else 0,
        'stored_api_requests':record['request_count'],'semantic_accuracy':'NOT_MEASURED'}
    if 'provenance' in value:
        actual=record['request_count'] if record_origin=='RUN' and record['origin']=='OPENAI_API' else 0
        value['provenance'].update(source_network_requests=value['provenance']['network_requests'],
                                   external_ai_requests=actual,model_calls=actual)
        value['provenance']['network_requests']+=actual
    # Legacy MEMO/P4/23-factor data is not rewritten by the cloud output.
    return value


def build(ticker,as_of=None,*,usage_mode='research',ai=False,ai_dry_run=False,config=None,
          saved=None,record_origin='RUN',cloud_options=None,**kwargs):
    if kwargs.get('fetch_missing'):raise ValueError('P6_USES_LOCAL_SOURCES_FETCH_MISSING_NOT_SUPPORTED')
    value,snapshot=core.build(ticker,as_of,ai=False,ai_dry_run=False,**kwargs)
    enrich(value,mode=usage_mode,ai=ai,dry_run=ai_dry_run,config=config,saved=saved,
           record_origin=record_origin,**(cloud_options or {}))
    core.validate(value);return value,snapshot


def rebuild(value,snapshot):
    p=value['professional_research']
    return build(value['ticker'],value['analysis_as_of'],snapshot=snapshot,illustrative=value['is_illustrative'],
        market_snapshot=value['market_snapshot'],sec_acquisition=value.get('sec_acquisition'),
        scenario_horizon=value.get('scenario_horizon',1),scenario_assumptions=value.get('scenario_override_inputs'),
        peer_inputs=value.get('peer_inputs',[]),usage_mode=p['mode'],ai=p['ai_requested'],ai_dry_run=p['dry_run'],
        config=p['config'],saved=p['record'],record_origin=p['record_origin'])[0]
