"""Source-bound financial context and explicitly graded economic exposure."""
from datetime import date
from decimal import Decimal
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import digest,stable_hash

BASE=Path(__file__).resolve().parent
FILES=['extensions/'+n for n in ('exposure_model.py','exposure_policy.json','exposure_contract.schema.json','exposure_report.py')]
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def methods(): return {p:digest(ROOT/p) for p in FILES}
def policy():
    p=read(BASE/'exposure_policy.json')
    if p['external_calls'] or p['training'] or p['score_release'] or p['network_requests']: raise ValueError('EXPOSURE_BOUNDARY_CHANGED')
    return p


def eligible(f,cik,at,spec):
    bound=as_utc(at).replace(microsecond=0)
    if f['cik']!=cik: raise ValueError('EXPOSURE_ISSUER_MIX')
    if f['validation_kind'] not in {'recorded_source_review','deterministic_source_match'}: return False
    if not Decimal(f['value']).is_finite() or f['unit'] not in spec['units'] or f['scope'] not in spec['scopes'] or f['basis'] not in spec['bases']: return False
    end=date.fromisoformat(f['end']); start=date.fromisoformat(f['start']) if f['start'] else None
    if as_utc(f['available_at'])>=bound or end>bound.date() or start and start>end: return False
    if (bound.date()-end).days>(400 if f['basis']=='annual' else 120): return False
    if f['basis']=='instant' and start is not None or f['basis']!='instant' and start is None: return False
    refs=f['source_refs']
    return bool(refs) and all(urlsplit(r['url']).scheme=='https' and urlsplit(r['url']).netloc in {'www.sec.gov','data.sec.gov'} and re.fullmatch(r'[a-f0-9]{64}',r['raw_sha256']) and r['source_id'] and r['locator'] for r in refs)


def row(name,spec,facts,cik,at):
    selected=[f for f in facts if f['metric'] in spec['metrics'] and eligible(f,cik,at,spec)]
    base={'id':name,'kind':spec['kind'],'label_ko':spec['label_ko'],'status':'UNKNOWN','value':None,'unit':None,'basis':None,'scope':None,'effective_period':None,'published_at':None,'publication_time_verified':False,'first_available_at':None,'fact_ids':[],'source_refs':[],'assumptions':[],'invalidation_conditions':['source_or_review_changed','new_amendment','stale_or_conflicting_period'],'reason_code':'no_eligible_reviewed_evidence'}
    if not selected: return base
    end=max(f['end'] for f in selected); selected=[f for f in selected if f['end']==end]
    if any(f['basis']=='quarter' for f in selected): selected=[f for f in selected if f['basis']=='quarter']
    signatures={(f['value'],f['unit'],f['basis'],f['start'],f['end'],f['scope']) for f in selected}
    if len(signatures)!=1:
        return {**base,'reason_code':'conflicting_values_or_scopes','fact_ids':[f['fact_id'] for f in selected],'source_refs':[r for f in selected for r in f['source_refs']]}
    f=sorted(selected,key=lambda f:f['fact_id'])[0]
    status='INFERRED' if spec.get('role_inference') else 'VERIFIED'
    if spec.get('role_inference') and Decimal(f['value'])<=0: return {**base,'reason_code':'nonpositive_amount_does_not_support_role'}
    return {**base,'status':status,'value':f['value'],'unit':f['unit'],'basis':f['basis'],'scope':f['scope'],'effective_period':{'start':f['start'],'end':f['end']},'first_available_at':f['available_at'],'fact_ids':[f['fact_id']],'source_refs':f['source_refs'],'reason_code':'positive_specific_activity_implies_limited_role' if spec.get('role_inference') else 'direct_reviewed_numeric_fact','assumptions':['activity_definition_and_period_limited; no unhedged sensitivity implied'] if spec.get('role_inference') else []}


def derive(ai,at):
    if as_utc(ai['as_of'])>as_utc(at): raise ValueError('EXPOSURE_FUTURE_INPUT')
    facts=ai['financial_facts']; cik=ai['identity']['cik']
    if any(f['cik']!=cik for f in facts) or len({f['fact_id'] for f in facts})!=len(facts): raise ValueError('EXPOSURE_IDENTITY_OR_DUPLICATE_FACT')
    items=[row(k,s,facts,cik,at) for k,s in policy()['properties'].items()]
    lookup={x['id']:x for x in items}; hypotheses=[]
    for ident,required,assumption in [('financing_cost','liabilities_context','interest_bearing_rate_sensitive_debt_and_reset_timing_unverified'),('cash_yield','cash_balance','interest_earning_cash_and_repricing_unverified'),('investment_budget','capital_expenditure_context','incremental_financing_and_funding_source_unverified')]:
        r=lookup[required]
        if r['status']=='VERIFIED' and Decimal(r['value'])>0:
            hypotheses.append({'id':ident,'status':'HYPOTHETICAL','context_fact_ids':r['fact_ids'],'assumptions':[assumption],'business_exposure_verified':False,'five_session_effect':None})
    return {'contract':'issuer-exposure-v1','identity':ai['identity'],'is_illustrative':ai['is_illustrative'],'as_of':at,'original_input_as_of':ai['as_of'],'input_sha256':stable_hash(ai),'items':items,'hypotheses':hypotheses,'coverage':{s:sum(x['status']==s for x in items) for s in ('VERIFIED','INFERRED','UNKNOWN')},'verified_business_exposures':sum(x['status']=='VERIFIED' and x['kind']=='economic_exposure' for x in items),'business_exposure_complete':False,'released_score':None,'released_price_plan':None,'training':False,'external_calls':0}
