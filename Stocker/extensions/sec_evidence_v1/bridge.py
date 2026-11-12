"""Only revalidated exposures enter the unchanged financial-impact-v1 contract."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from research.rights import as_utc
from research.validation import stable_hash,digest
from extensions.financial_impact_v1 import report as impact,contract as impact_contract
from extensions.financial_impact_v1.models import BINDINGS
from . import reviews,verify_seal,methods


def missing_input(result):
    identity=result['identity'];start=as_utc(result['as_of']).date();end=start+timedelta(days=365)
    dates=[r['fact']['end'] for r in result['reviewed_financial_facts'] if r['fact']['end']]
    currencies={c['currency'] for c in result['candidates'] if c['currency']}
    currency=next(iter(currencies)) if len(currencies)==1 else 'XXX'
    period={'start':None,'end':max(dates) if dates else start.isoformat(),'basis':'INSTANT'}
    scope='UNKNOWN_CONTRACT_ACCOUNTING_SCOPE'
    return {'contract':'financial-impact-input-v1','mode':'ILLUSTRATIVE' if result['is_illustrative'] else 'REAL',
        'context':{**identity,'as_of':result['as_of'],'currency':currency,'accounting_scope':scope,'reporting_period':period,
            'calculation_start':start.isoformat(),'calculation_end':end.isoformat()},
        'event':{'event_id':'MISSING_VERIFIED_RATE_EVENT','canonical_event_id':'MISSING_VERIFIED_RATE_EVENT',
            'event_type':'INTEREST_RATE_CHANGE','economic_variable':'policy_rate','rate_reference':None,'rate_currency':None,
            'delta':None,'effective_date':None,'magnitude_fact_id':None,'effective_fact_id':None,'verification_status':'UNVERIFIED',
            'uncertainties':['SEC contract discovery does not verify an economic rate event or its magnitude.']},
        'exposure':{'exposure_id':'unverified_'+identity['cik'],'instrument_id':'MISSING_VERIFIED_CONTRACT',**identity,
            'currency':currency,'accounting_scope':scope,'reporting_period':period,'verification_status':'UNVERIFIED',
            'bindings':{k:None for k in BINDINGS}},
        'assumptions':{'scenario_id':'sec_evidence_probe','label':'Base','rate_delta_override':None,
            'description':'Explicit 365-day evidence-coverage probe; no event or actual financial loss asserted.',
            'valid_if':['Two current independent owner-trusted contract reviews and a separately verified rate event.'],
            'invalid_if':['Substituting liabilities, maturity, expense or swap notional for floating-rate principal.'],'probability':None},
        'policy':{'version':'financial-impact-policy-v1','day_count':None,'precision':50,'rounding':'ROUND_HALF_EVEN',
            'max_source_age_days':730,'trusted_review_hashes':[]},'sources':[],'facts':[],'prior_calculations':[],
        'context_facts':deepcopy(result['reviewed_financial_facts']),'origin':None}


def calculate(result,trust,*,event_input=None,instrument_id=None,created_at=None):
    verify_seal(result)
    if result['methods']!=methods():raise ValueError('ADAPTER_METHOD_CHANGED')
    approved,conflicts,_=reviews.promote(result,result['review_store'],trust)
    if approved!=result['verified_exposures'] or conflicts!=result['conflicts']:raise ValueError('UNTRUSTED_VERIFIED_EXPOSURE')
    packet=deepcopy(event_input) if event_input is not None else missing_input(result)
    impact_contract.validate(packet)
    if packet['mode']!=('ILLUSTRATIVE' if result['is_illustrative'] else 'REAL') or (packet['context']['cik'],packet['context']['ticker'],packet['context']['as_of'])!=(result['identity']['cik'],result['identity']['ticker'],result['as_of']):raise ValueError('EVENT_INPUT_IDENTITY_OR_CUTOFF_MISMATCH')
    if any(s['issuer'] is not None for s in packet['sources']) or any(not f['kind'].startswith('event_') for f in packet['facts']):raise ValueError('EVENT_INPUT_CANNOT_SMUGGLE_ISSUER_FACTS')
    choices=[v for v in approved if instrument_id is None or v['instrument_id']==instrument_id]
    selection_issue=None
    if len(choices)>1:
        selection_issue='MULTIPLE_VERIFIED_INSTRUMENTS_NO_AUTOMATIC_AGGREGATION';choices=[]
    if instrument_id is not None and not choices:raise ValueError('VERIFIED_INSTRUMENT_NOT_FOUND')
    if choices:
        v=choices[0];x=v['exposure']
        if event_input is not None and any(packet['context'][k]!=x[k] for k in ['currency','accounting_scope','reporting_period']):raise ValueError('EVENT_CONTEXT_EXPOSURE_SCOPE_MISMATCH')
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
    value=impact.analyze(packet,created_at=created_at)
    return {'adapter_result_sha256':result['record_sha256'],'trust_policy_sha256':stable_hash(trust),
        'input':packet,'output':value,'independent_contract_receipts':[h for v in choices for h in v['review_receipt_hashes']],
        'selection_issue':selection_issue,'machine_binding_certificates_are_not_human_reviews':True,'new_model_calls':0}
