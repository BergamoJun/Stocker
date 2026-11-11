"""Issuer-specific ledgers and targeted offline SEC review, never source-text prompts."""
import re
from research.validation import stable_hash
from . import boundaries
from .. import exposure_v3, research_v3_contract as legacy

STATES={'VERIFIED','INFERRED','UNKNOWN','CONFLICTED','NOT_APPLICABLE'}
TARGETS={'interest_bearing_debt':r'Debt|Borrow','floating_rate_exposure':r'Variable|Floating',
    'refinancing_schedule':r'Repric|Refinanc','debt_maturity':r'Debt.*(?:Current|Matur)|Matur',
    'interest_earning_cash':r'Interest.*(?:Income|Cash)|Cash', 'repricing_terms':r'Interest|Repric',
    'interest_rate_hedge_terms':r'Hedge|Swap|Derivative','fixed_rate_effect':r'Fixed|Hedge|Swap',
    'energy_procurement_exposure':r'Oil|Gas|Energy|Electric|Purchase','energy_sales_exposure':r'Oil|Gas|Energy|Sales',
    'relevant_purchase_contract_terms':r'Contract|Purchase','relevant_sale_contract_terms':r'Contract|Sale',
    'purchase_price_sensitivity':r'Sensitiv|Commodity|Derivative','sale_price_sensitivity':r'Sensitiv|Commodity|Derivative',
    'demand_linkage':r'Demand|Contract|Volume','supply_linkage':r'Supply|Contract|Volume','actual_energy_sales_activity':r'Oil|Gas|Energy'}


def assess(ai,propositions,offline_candidates):
    ledger=exposure_v3.derive(ai,ai['as_of'])
    requirements=list(ledger['requirements'])
    existing={r['requirement_id'] for r in requirements}
    for name in TARGETS:
        if name not in existing:requirements.append({'requirement_id':name,'status':'UNKNOWN','fact_ids':[],
            'source_ids':[],'reason':'no_reviewed_component_evidence'})
    queue=[]
    for requirement in requirements:
        name=requirement['requirement_id'];pattern=TARGETS.get(name,'Contract')
        matches=[r for r in offline_candidates if re.search(pattern,r['locator'].get('tag') or '',re.I)]
        if any(r['cik']!=ai['identity']['cik'] for r in matches):raise ValueError('V4_EXPOSURE_CIK_MIX')
        queue.append({'cik':ai['identity']['cik'],'requirement_id':name,'current_state':requirement['status'],
            'candidate_ids':[r['record_sha256'] for r in matches], 'candidate_count':len(matches),
            'review_state':'PENDING_HUMAN_COMPONENT_REVIEW','candidate_is_evidence':False,
            'needed_review':['exact definition','unit/period/scope/axis','contract repricing or price linkage',
                             'hedge/fixed effects','source version and first local availability'],
            'not_applicable_requires':'affirmative source-backed review, never absence of a tag'})
    by_req={r['requirement_id']:r for r in requirements};routes=[]
    for p in propositions:
        raw=p['raw']
        for route,spec in legacy.policy()['routes'].items():
            if raw['event_type']!=spec['event_type']:continue
            needed=[by_req[n] for n in spec['requirements']]
            eligible=p['decision']=='SUPPORTED' and raw['direction'] in {'increase','decrease'} and all(r['status']=='VERIFIED' for r in needed)
            routes.append({'route_id':route,'source_proposition_id':p.get('proposition_id'),'cik':ai['identity']['cik'],
                'status':'PROVISIONAL_CONDITIONAL' if eligible else 'HYPOTHETICAL','requirements':needed,
                'condition_realization':'NOT_ESTABLISHED','independently_validated':False,'net_effect':'UNKNOWN',
                'stock_effect':None,'effect_magnitude':None,'probability':None})
    return {'identity':ai['identity'],'input_sha256':stable_hash(ai),'as_of':ai['as_of'],
            'requirements':requirements,'review_queue':queue,'routes':routes,
            'reviewed_contracts_added':0,'human_decisions_completed':0,
            'legacy_numeric_facts':ledger['legacy_exposure']['items'],**boundaries()}
