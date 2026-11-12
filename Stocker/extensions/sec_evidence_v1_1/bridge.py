"""Only source-supported AND currently eligible facts reach unchanged Impact v1."""
from copy import deepcopy
from pathlib import Path
from research.validation import digest,stable_hash
from extensions.financial_impact_v1 import report as impact,contract as impact_contract
from extensions.sec_evidence_v1.bridge import missing_input
from . import discovery


def calculate(result,trust,*,event_input=None,instrument_id=None,created_at=None):
    discovery.revalidate(result,trust)
    packet=deepcopy(event_input) if event_input is not None else missing_input(result)
    impact_contract.validate(packet)
    if packet['mode']!=('ILLUSTRATIVE' if result['is_illustrative'] else 'REAL') or (packet['context']['cik'],packet['context']['ticker'],packet['context']['as_of'])!=(result['identity']['cik'],result['identity']['ticker'],result['as_of']):
        raise ValueError('EVENT_INPUT_IDENTITY_OR_CUTOFF_MISMATCH')
    if any(s['issuer'] is not None for s in packet['sources']) or any(not f['kind'].startswith('event_') for f in packet['facts']):
        raise ValueError('EVENT_INPUT_CANNOT_SMUGGLE_ISSUER_FACTS')
    window={'start':packet['context']['calculation_start'],'end':packet['context']['calculation_end']}
    if window!=result['eligibility_window']:raise ValueError('ELIGIBILITY_CALCULATION_WINDOW_MISMATCH')
    if event_input is not None and packet['event']['effective_date']!=result['configuration']['event_effective']:
        raise ValueError('ELIGIBILITY_EVENT_EFFECTIVE_MISMATCH')
    choices=[v for v in result['verified_exposures'] if instrument_id is None or v['instrument_id']==instrument_id]
    selection_issue=None
    if len(choices)>1:selection_issue='MULTIPLE_ELIGIBLE_INSTRUMENTS_NO_AUTOMATIC_AGGREGATION';choices=[]
    if instrument_id is not None and not choices:selection_issue='INSTRUMENT_NOT_CALCULATION_ELIGIBLE'
    if choices:
        v=choices[0];x=v['exposure']
        if event_input is not None and any(packet['context'][k]!=x[k] for k in ['currency','accounting_scope','reporting_period']):
            raise ValueError('EVENT_CONTEXT_EXPOSURE_SCOPE_MISMATCH')
        packet['exposure']=deepcopy(x)
        for name in ['currency','accounting_scope','reporting_period']:packet['context'][name]=deepcopy(x[name])
        keys={f['source_id'] for f in v['facts']}
        for s in result['sources']:
            if s['source_key'] not in keys:continue
            path=Path(s['raw_path'])
            if digest(path)!=s['original_sha256']:raise ValueError('BRIDGE_ORIGINAL_BYTES_CHANGED')
            text=path.read_bytes().decode('utf-8')
            if len(text)>2000000:raise ValueError('FROZEN_IMPACT_SOURCE_SIZE_UNSUPPORTED')
            packet['sources'].append({'source_id':s['source_key'],'url':s['source_url'],'original_sha256':s['original_sha256'],
                'text':text,'published_at':s['published_at'],'available_at':s['available_at'],'is_illustrative':result['is_illustrative'],
                'issuer':deepcopy(result['identity']),'rights':{'local_calculation':True,'redistribution':False}})
        packet['facts']+=deepcopy(v['facts'])
        packet['policy']['day_count']=next(f['value'] for f in v['facts'] if f['kind']=='day_count')
    else:
        packet['exposure']=missing_input(result)['exposure']
        for name in ['cik','ticker','currency','accounting_scope','reporting_period']:
            packet['exposure'][name]=deepcopy(packet['context'][name])
    packet['policy']['trusted_review_hashes']=list(trust['trusted_event_review_hashes'])+[
        f['review']['record_sha256'] for v in choices for f in v['facts'] if f['review'] is not None]
    packet['context_facts']=deepcopy(result['reviewed_financial_facts']);packet['origin']=None
    return {'adapter_result_sha256':result['record_sha256'],'trust_policy_sha256':stable_hash(trust),
        'input':packet,'output':impact.analyze(packet,created_at=created_at),
        'independent_contract_receipts':[h for v in choices for h in v['review_receipt_hashes']],
        'selection_issue':selection_issue,'machine_binding_certificates_are_not_human_reviews':True,'new_model_calls':0}
