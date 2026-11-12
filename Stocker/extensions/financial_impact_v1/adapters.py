"""Read-only missing-contract probes from frozen, approved local issuer inputs."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import digest
from extensions import research_v3_contract as legacy
from extensions.research_v4 import read
from .models import BINDINGS


def issuer_probe(ticker):
    if ticker not in {'BE','MSFT','XOM'}:raise ValueError('UNSUPPORTED_FROZEN_ISSUER')
    path=ROOT/'reports/private/maturation_case/20261111/pilots'/ticker/'SOURCE_INPUT.json'
    ledger=ROOT/'reports/private/research_v4_case/20261111/exposure'/(ticker+'.final.json')
    ai=read(path);legacy.validate_ai(ai,ai['as_of']);exposure=read(ledger)
    if ai['is_illustrative'] or ai['identity']['symbol']!=ticker or exposure['identity']!=ai['identity'] or exposure['as_of']!=ai['as_of']:
        raise ValueError('FROZEN_ISSUER_SOURCE_MISMATCH')
    # No ordinary financial metric, including liabilities or sales, is promoted
    # to a repricing principal or a contractual rate/hedge fact.
    currencies={f['unit'] for f in ai['financial_facts'] if len(f['unit'])==3}
    if len(currencies)!=1:raise ValueError('NO_UNAMBIGUOUS_CONTEXT_CURRENCY')
    currency=next(iter(currencies));start=as_utc(ai['as_of']).date();end=start+timedelta(days=365)
    period={'start':None,'end':max(f['end'] for f in ai['financial_facts']),'basis':'INSTANT'}
    identity=ai['identity'];scope='UNVERIFIED_INTEREST_EXPENSE_SCOPE'
    return {'contract':'financial-impact-input-v1','mode':'REAL',
        'context':{'cik':identity['cik'],'ticker':ticker,'as_of':ai['as_of'],'currency':currency,
            'accounting_scope':scope,'reporting_period':period,'calculation_start':start.isoformat(),'calculation_end':end.isoformat()},
        'event':{'event_id':'UNVERIFIED_RATE_EVENT','canonical_event_id':'UNVERIFIED_RATE_EVENT',
            'event_type':'INTEREST_RATE_CHANGE','economic_variable':'policy_rate','rate_reference':None,'rate_currency':None,'delta':None,'effective_date':None,
            'magnitude_fact_id':None,'effective_fact_id':None,'verification_status':'UNVERIFIED',
            'uncertainties':['This is a missing-evidence probe, not an asserted historical rate event.',
                'Existing model consistency does not verify event size, contractual exposure or actual earnings.']},
        'exposure':{'exposure_id':'unverified_'+identity['cik'],'instrument_id':'MISSING_CONTRACT_INSTRUMENT',
            'cik':identity['cik'],'ticker':ticker,'currency':currency,'accounting_scope':scope,'reporting_period':deepcopy(period),
            'verification_status':'UNVERIFIED','bindings':{k:None for k in BINDINGS}},
        'assumptions':{'scenario_id':'missing_evidence_probe','label':'Base','rate_delta_override':None,
            'description':'Read-only contract-coverage probe over an explicit 365-day diagnostic window; no actual event or loss asserted.',
            'valid_if':['Verified event, instrument principal, repricing, linkage, reset, day-count, hedge and accounting evidence are required.'],
            'invalid_if':['Using total liabilities, total cash, general revenue or unreviewed SEC tag matches as contract evidence.'],'probability':None},
        'policy':{'version':'financial-impact-policy-v1','day_count':None,'precision':50,'rounding':'ROUND_HALF_EVEN',
            'max_source_age_days':90,'trusted_review_hashes':[]},
        'sources':[],'facts':[],'prior_calculations':[],'context_facts':deepcopy(ai['financial_facts']),
        'origin':{'source_input_path':str(path.relative_to(ROOT)).replace('\\','/'),'source_input_sha256':digest(path),
            'exposure_ledger_path':str(ledger.relative_to(ROOT)).replace('\\','/'),'exposure_ledger_sha256':digest(ledger)}}


def verify_origin(packet):
    origin=packet['origin']
    if origin is None:return
    for field in ['source_input','exposure_ledger']:
        path=(ROOT/origin[field+'_path']).resolve()
        if not path.is_relative_to((ROOT/'reports/private').resolve()) or not path.is_file() or digest(path)!=origin[field+'_sha256']:
            raise ValueError('IMPACT_ORIGIN_CHANGED')
    expected=issuer_probe(packet['context']['ticker'])
    if packet['context_facts']!=expected['context_facts'] or packet['context']!=expected['context'] or origin!=expected['origin']:
        raise ValueError('IMPACT_ORIGIN_CONTEXT_CHANGED')
