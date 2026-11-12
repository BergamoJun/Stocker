"""ILLUSTRATIVE NOT LIVE DATA. Synthetic contracts, never independent evidence."""
from copy import deepcopy
from hashlib import sha256
from .models import BINDINGS


def interest_example():
    period={'start':None,'end':'2025-12-31','basis':'INSTANT'}
    ctx={'cik':'0000000001','ticker':'DEMO','as_of':'2026-01-01T12:00:00Z','currency':'USD',
        'accounting_scope':'ILLUSTRATIVE_CONSOLIDATED','reporting_period':period,
        'calculation_start':'2026-01-01','calculation_end':'2027-01-01'}
    values={'interest_bearing':(True,'boolean'),'principal':('1000000000','CURRENCY'),
        'repricing_fraction':('1','fraction'),'rate_mapping':('1','fraction'),
        'reset_date':('2026-01-01','date'),'day_count':('ACT/365F-v1','convention'),
        'hedged_fraction':('0','fraction'),'hedge_terms':('NONE','contract'),
        'principal_schedule':('CONSTANT_CONTRACTUAL','contract'),'accounting_treatment':('EXPENSED_PRETAX','contract')}
    rows=[('event_delta','25','bp',True),('event_effective_date','2026-01-01','date',True)]
    rows += [(BINDINGS[k],v,u,False) for k,(v,u) in values.items()]
    texts={event:'ILLUSTRATIVE NOT LIVE DATA.\n'+'\n'.join(f'{kind}: {v} {unit}' for kind,v,unit,is_event in rows if is_event==event) for event in [True,False]}
    sources=[]
    for event,text in texts.items():
        sources.append({'source_id':'event_source' if event else 'loan_source','url':'https://example.invalid/ILLUSTRATIVE/'+('event' if event else 'loan'),
            'original_sha256':sha256(text.encode()).hexdigest(),'text':text,'published_at':'2025-12-31T00:00:00Z',
            'available_at':'2025-12-31T12:00:00Z','is_illustrative':True,
            'issuer':None if event else {'cik':ctx['cik'],'ticker':ctx['ticker']},
            'rights':{'local_calculation':True,'redistribution':False}})
    facts=[]
    for kind,value,unit,event in rows:
        source=sources[0 if event else 1];quote=f'{kind}: {value} {unit}';start=source['text'].index(quote)
        facts.append({'fact_id':'demo_'+kind,'source_id':source['source_id'],'original_sha256':source['original_sha256'],
            'kind':kind,'value':value,'unit':unit,'currency':None if event else 'USD',
            'cik':None if event else ctx['cik'],'ticker':None if event else ctx['ticker'],'instrument_id':None if event else 'demo_loan',
            'accounting_scope':None if event else ctx['accounting_scope'],'reporting_period':None if event else deepcopy(period),
            'valid_from':None if event else ctx['calculation_start'],'valid_to':None if event else ctx['calculation_end'],
            'available_at':'2025-12-31T12:00:00Z','exact_quote':quote,'start_offset':start,'end_offset':start+len(quote),
            'verification_status':'ILLUSTRATIVE','review':None,'event_reference':'ILLUSTRATIVE_USD_POLICY_RATE' if event else None,'rate_currency':'USD' if event else None,
            'rate_link':{'benchmark':'ILLUSTRATIVE_CONTRACT_INDEX','event_variable':'policy_rate','event_reference':'ILLUSTRATIVE_USD_POLICY_RATE','rate_currency':'USD','minimum_delta_bp':'-100','maximum_delta_bp':'100'} if kind=='contract_rate_mapping' else None})
    return {'contract':'financial-impact-input-v1','mode':'ILLUSTRATIVE','context':ctx,
        'event':{'event_id':'demo_event','canonical_event_id':'demo_canonical_event','event_type':'INTEREST_RATE_CHANGE',
            'economic_variable':'policy_rate','rate_reference':'ILLUSTRATIVE_USD_POLICY_RATE','rate_currency':'USD','delta':{'value':'25','unit':'bp'},'effective_date':'2026-01-01',
            'magnitude_fact_id':'demo_event_delta','effective_fact_id':'demo_event_effective_date',
            'verification_status':'ILLUSTRATIVE','uncertainties':['Synthetic scenario only; no actual issuer contract is established.']},
        'exposure':{'exposure_id':'demo_exposure','instrument_id':'demo_loan','cik':ctx['cik'],'ticker':ctx['ticker'],
            'currency':'USD','accounting_scope':ctx['accounting_scope'],'reporting_period':deepcopy(period),
            'verification_status':'ILLUSTRATIVE','bindings':{k:'demo_'+v for k,v in BINDINGS.items()}},
        'assumptions':{'scenario_id':'demo_original','label':'Base','rate_delta_override':None,
            'description':'ILLUSTRATIVE +25bp, USD 1bn, full repricing, no hedge, 365 actual days / 365.',
            'valid_if':['Constant repricing principal and a documented linear one-for-one contractual mapping.'],
            'invalid_if':['A different contract, amortization, hedge, cap, floor or accounting scope.'],'probability':None},
        'policy':{'version':'financial-impact-policy-v1','day_count':'ACT/365F-v1','precision':50,
            'rounding':'ROUND_HALF_EVEN','max_source_age_days':30,'trusted_review_hashes':[]},
        'sources':sources,'facts':facts,'prior_calculations':[],'context_facts':[],'origin':None}
