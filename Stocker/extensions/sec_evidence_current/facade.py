"""Official request -> hard-coded v1.1 -> fresh source/trust authorization."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import re
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import stable_hash
from extensions.sec_evidence_v1_1 import discovery,reviews
from . import VERIFIER,Rejected,seal,methods,contract
from .owner import context


def request_for(cik,ticker,as_of,*,illustrative=False,event_input=None,instrument_id=None,calculation_window=None,**options):
    at=as_utc(as_of).date()
    window=calculation_window or ({'start':event_input['context']['calculation_start'],'end':event_input['context']['calculation_end']} if event_input is not None else {'start':at.isoformat(),'end':(at+timedelta(days=365)).isoformat()})
    settings=dict(source_store=None,legacy_review_root=None,review_store=None,cache_root=None,
                  max_age_days=730,max_filings=4,maximum_candidates=512)
    if set(options)-set(settings):raise Rejected('UNSUPPORTED_SOURCE_OPTION')
    settings.update({k:str(v) if isinstance(v,Path) else v for k,v in options.items()})
    return {'contract':'sec-evidence-current-request-v1','verification_version':VERIFIER,
        'mode':'ILLUSTRATIVE' if illustrative else 'REAL','cik':cik,'ticker':ticker,'as_of':as_of,
        'calculation_window':deepcopy(window),'event_input':deepcopy(event_input),'instrument_id':instrument_id,'source_options':settings}


def authorize(request,*,owner=None):
    contract.validate(request)
    illustrative=request['mode']=='ILLUSTRATIVE';owner=context(owner,illustrative);trust=owner.trust(illustrative)
    event=request['event_input'];window=request['calculation_window']
    if event is not None:
        c=event['context']
        if (c['cik'],c['ticker'],c['as_of'])!=(request['cik'],request['ticker'],request['as_of']):raise Rejected('EVENT_ISSUER_OR_ASOF_MISMATCH')
        if {'start':c['calculation_start'],'end':c['calculation_end']}!=window:raise Rejected('EVENT_WINDOW_MISMATCH')
        if any(s['issuer'] is not None for s in event['sources']) or any(not f['kind'].startswith('event_') for f in event['facts']):
            raise Rejected('EVENT_INPUT_CANNOT_SUPPLY_ISSUER_APPROVAL')
    result=discovery.build(request['cik'],request['ticker'],request['as_of'],illustrative=illustrative,
        trust=trust,calculation_window=window,event_effective=event['event']['effective_date'] if event else None,**request['source_options'])
    if result['contract']!=VERIFIER:raise Rejected('CURRENT_VERIFIER_RESULT_REQUIRED')
    certificate=seal({'contract':'sec-current-verification-v1','verification_version':VERIFIER,'facade_methods':methods(),
        'request_sha256':stable_hash(request),'result_sha256':result['record_sha256'],
        'owner_binding_sha256':stable_hash(owner.binding(illustrative)),'trust_sha256':stable_hash(trust),
        'event_sha256':stable_hash(event),'source_binding_sha256':stable_hash([
            [s['source_key'],s['original_sha256'],s['incarnation_id']] for s in result['sources']]),
        'as_of':request['as_of'],'calculation_window':deepcopy(window)})
    contract.validate(certificate,'certificate')
    return {'request':deepcopy(request),'result':result,'certificate':certificate}


def process(request,*,owner=None,created_at=None):
    from .bridge import calculate
    try:
        authorization=authorize(request,owner=owner)
        calculation=calculate(request,authorization['result'],authorization['certificate'],owner=owner,created_at=created_at)
        o=calculation['output'];r=authorization['result'];codes=[i['code'] for i in o['issues']]
        codes += [i['code'] for row in r['semantic_assessments'] for i in row['semantic']['issues']]
        codes += [code for row in r['semantic_assessments'] if not row['eligibility']['calculation_eligible'] for code in row['eligibility']['eligibility_reason']]
        if not r['verified_exposures']:codes.append('NO_CALCULATION_ELIGIBLE_CONTRACT')
        trust=context(owner,request['mode']=='ILLUSTRATIVE').trust(request['mode']=='ILLUSTRATIVE')
        if not trust['trusted_review_hashes']:codes.append('OWNER_TRUST_ALLOWLIST_EMPTY')
        return {'contract':'sec-current-decision-v1','verification_version':VERIFIER,'status':'CALCULATED' if o['amount'] is not None else 'WITHHELD',
            'amount':o['amount'],'calculation_status':o['status'],'reason_codes':sorted(set(codes)),
            'reviewer_required':o['amount'] is None,'authorization':authorization,'bridge':calculation,'error_class':None}
    except Exception as exc:
        # Unexpected verifier failures also fail closed; no alternate engine is
        # selected. The error class is retained so outages are not quality passes.
        code=exc.code if isinstance(exc,Rejected) else str(exc).split(':',1)[0]
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{2,119}',code):code='VALIDATOR_EXECUTION_FAILED'
        return {'contract':'sec-current-decision-v1','verification_version':VERIFIER,'status':'BLOCKED','amount':None,
            'calculation_status':'INSUFFICIENT_EVIDENCE','reason_codes':[code],
            'reviewer_required':True,'authorization':None,'bridge':None,'error_class':type(exc).__name__}


def accept_review(request,manifest,review_store,*,owner=None,clock=None):
    target=Path(review_store).resolve()
    if not target.is_relative_to((ROOT/'reports/private/sec_current_reviews').resolve()):
        raise Rejected('OWNED_CURRENT_REVIEW_STORE_REQUIRED')
    authorization=authorize(request,owner=owner)
    if clock is not None and request['mode']!='ILLUSTRATIVE':raise Rejected('REAL_REVIEW_CLOCK_OVERRIDE_FORBIDDEN')
    options={} if clock is None else {'clock':clock}
    return reviews.store(manifest,authorization['result'],target,**options)
