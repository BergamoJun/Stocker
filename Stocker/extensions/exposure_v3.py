"""Connect the existing exposure model to explicit per-route requirements."""
from decimal import Decimal

from research.validation import stable_hash
from . import exposure_model as legacy, research_v3_contract as contract

# These require exact, source-reviewed numeric propositions. Generic revenue,
# balances, industry identity and hedge notionals cannot fill them. No actual
# observations of these metrics are fabricated or collected by this adapter.
DIRECT = {
    'refinancing_schedule': ('debt_contractual_repricing_within_one_year', 'instant'),
    'interest_earning_cash': ('interest_earning_cash', 'instant'),
    'repricing_terms': ('cash_contractual_repricing_within_one_year', 'instant'),
    'energy_procurement_exposure': ('energy_procurement_contract_amount', 'annual'),
    'purchase_price_sensitivity': ('energy_purchase_cost_sensitivity_1pct', 'sensitivity_1pct'),
    'relevant_purchase_contract_terms': ('energy_purchase_price_linked_contract_amount', 'annual'),
    'energy_sales_exposure': ('energy_sales_contract_amount', 'annual'),
    'actual_energy_sales_activity': ('energy_sales_contract_amount', 'annual'),
    'sale_price_sensitivity': ('energy_sales_revenue_sensitivity_1pct', 'sensitivity_1pct'),
    'relevant_sale_contract_terms': ('energy_sales_price_linked_contract_amount', 'annual'),
    'demand_linkage': ('energy_demand_linked_sales_amount', 'annual'),
    'supply_linkage': ('energy_supply_linked_purchase_amount', 'annual'),
}


def derive(ai, at):
    original = legacy.derive(ai, at)
    old = {r['id']: r for r in original['items']}
    policy = contract.policy()
    names = sorted({p for r in policy['routes'].values() for p in r['requirements']})
    requirements = []
    for name in names:
        rows = [old[k] for k in policy['legacy_property_links'].get(name, [])]
        if name in DIRECT:
            metric, basis = DIRECT[name]
            rows.append(legacy.row(name, {'metrics': [metric], 'kind': 'economic_exposure',
                        'label_ko': name, 'units': ['USD'], 'scopes': ['consolidated'],
                        'bases': [basis], 'role_inference': False}, ai['financial_facts'], ai['identity']['cik'], at))
        eligible = [r for r in rows if r['status'] in {'VERIFIED', 'INFERRED'} and r['value'] is not None and Decimal(r['value']) > 0]
        conflicts = [r for r in rows if r['reason_code'] == 'conflicting_values_or_scopes']
        direct = [r for r in eligible if r['id'] == name]
        selected = direct or eligible
        status = 'CONFLICTED' if conflicts else 'UNKNOWN'
        if selected and not conflicts:
            status = 'VERIFIED' if any(r['status'] == 'VERIFIED' for r in selected) else 'INFERRED'
            # A maturity balance is not a refinancing agreement; previous
            # interest income is not proof of current contractual cash terms.
            if not direct and name in {'refinancing_schedule', 'interest_earning_cash'}:
                status = 'INFERRED'
        references = selected + conflicts
        requirements.append({'requirement_id': name, 'status': status,
            'fact_ids': sorted({f for r in references for f in r['fact_ids']}),
            'source_ids': sorted({s['source_id'] for r in references for s in r['source_refs']}),
            'source_refs': [s for r in references for s in r['source_refs']],
            'propositions': [{'property_id': r['id'], 'value': r['value'], 'unit': r['unit'],
                              'basis': r['basis'], 'scope': r['scope'], 'effective_period': r['effective_period']} for r in references],
            'limitations': ['exact_component_only; net_hedge_effect_and_stock_sensitivity_unverified']})
    return {'contract': 'issuer-exposure-v3', 'legacy_exposure': original, 'requirements': requirements,
            'identity': ai['identity'], 'as_of': at, 'input_sha256': stable_hash(ai)}


def routes(ai, proposition, ledger):
    requirements = {r['requirement_id']: r for r in ledger['requirements']}
    applicable, hypothetical = [], []
    for route_id, spec in contract.policy()['routes'].items():
        if spec['event_type'] != proposition['event_type']:
            continue
        needed = [requirements[k] for k in spec['requirements']]
        statuses = {r['status'] for r in needed}
        exposure = ('CONFLICTED' if 'CONFLICTED' in statuses else 'VERIFIED' if statuses == {'VERIFIED'}
                    else 'INFERRED' if statuses <= {'VERIFIED', 'INFERRED'} else 'HYPOTHETICAL')
        direction = 'unknown'
        if proposition['variable_direction'] in {'increase', 'decrease'}:
            favourable = (proposition['variable_direction'] == 'increase') == spec['increase_favourable']
            direction = 'favourable' if favourable else 'adverse'
        supported = proposition['evidence_status'] == 'SEMANTICALLY_SUPPORTED' and direction != 'unknown'
        route = {'issuer_id': ai['identity']['cik'], 'event_id': proposition['proposition_id'],
            'route_id': route_id, 'mechanism': route_id + '_conditional_on_required_exposures',
            'economic_variable': proposition['economic_variable'], 'exposure_status': exposure,
            'supporting_fact_ids': sorted({f for r in needed for f in r['fact_ids']}),
            'supporting_source_ids': sorted({proposition['source_id']} | {s for r in needed for s in r['source_ids']}),
            'requirements': needed,
            'assumptions': [r['requirement_id'] for r in needed if r['status'] != 'VERIFIED'] +
                           ([proposition['condition_quote']] if proposition['condition_quote'] else []),
            'competing_mechanisms': [], 'economic_effect_direction': direction,
            'net_effect_status': 'UNKNOWN', 'horizon': 'unknown', 'stock_effect': None,
            'effect_magnitude': None, 'probability': None,
            'limitations': ['economic_mechanism_only', 'net_effect_and_market_expectations_unmeasured',
                            'verified_components_do_not_prove_net_business_sensitivity'],
            'invalidation_conditions': ['source_changed', 'exposure_contract_changed', 'opposite_evidence',
                                        'condition_not_met', 'stale_evidence'],
            'applicable': supported and exposure == 'VERIFIED'}
        (applicable if route['applicable'] else hypothetical).append(route)
    all_routes = applicable + hypothetical
    for r in all_routes:
        r['competing_mechanisms'] = [other['route_id'] for other in all_routes
            if other['economic_effect_direction'] not in {'unknown', r['economic_effect_direction']}]
    return {'applicable_routes': applicable, 'hypothetical_routes': hypothetical, 'net_effect': 'UNKNOWN'}
