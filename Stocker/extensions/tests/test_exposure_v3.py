"""ILLUSTRATIVE — removing/rebinding evidence must remove route eligibility."""
from copy import deepcopy
import hashlib
import pytest
import jsonschema

from extensions import analysis_contract, exposure_v3 as x, evidence_v3 as e, semantic_v3
from extensions.tests.test_analysis_contract import data, CUTOFF
from extensions.tests.test_semantic_v3 import proposal
from extensions import research_v3


def packet(text='Interest rates increased.', facts=None):
    identity, financial, docs, _ = data()
    if facts is not None:
        financial = facts
    p = docs[0]['paragraphs'][0]
    p.update(text=text, end=len(text), sha256=hashlib.sha256(text.encode()).hexdigest())
    docs[0]['text_sha256'] = p['sha256']
    ai, _ = analysis_contract.assemble(identity, CUTOFF, financial, docs, {}, illustrative=True)
    return ai


def fact(metric, **changes):
    _, financial, _, _ = data()
    f = deepcopy(financial[0])
    f.update({'fact_id': 'illustrative_' + metric, 'metric': metric, 'basis': 'instant', 'start': None, 'value': '100', **changes})
    return f


def proposition(ai, **changes):
    p = research_v3.payloads(ai)[0]
    q = p['text']
    return semantic_v3.bind({'propositions': [proposal(q, **changes)]}, p)[0]


def grounded():
    return packet(facts=[fact('total_borrowings'), fact('floating_rate_borrowings'), fact('debt_contractual_repricing_within_one_year')])


def test_generic_revenue_is_neither_energy_buyer_nor_seller():
    ai = packet('Oil prices increased.')
    p = proposition(ai, economic_subject='energy_prices', economic_variable='energy_price', event_type='commodity_price')
    ledger = x.derive(ai, CUTOFF)
    routes = x.routes(ai, p, ledger)
    assert not routes['applicable_routes']
    assert {r['route_id'] for r in routes['hypothetical_routes']} == {'energy_input_cost', 'energy_sales_price'}
    assert all(r['exposure_status'] == 'HYPOTHETICAL' for r in routes['hypothetical_routes'])
    assert {r['economic_effect_direction'] for r in routes['hypothetical_routes']} == {'favourable', 'adverse'}
    assert routes['net_effect'] == 'UNKNOWN'


def test_direct_component_evidence_can_support_opposing_routes_without_netting():
    metrics = ['energy_procurement_contract_amount', 'energy_purchase_cost_sensitivity_1pct',
               'energy_purchase_price_linked_contract_amount', 'energy_sales_contract_amount',
               'energy_sales_revenue_sensitivity_1pct', 'energy_sales_price_linked_contract_amount']
    facts = [fact(m, basis='sensitivity_1pct' if 'sensitivity' in m else 'annual', start='2026-01-01') for m in metrics]
    ai = packet('Oil prices increased.', facts)
    p = proposition(ai, economic_subject='energy_prices', economic_variable='energy_price', event_type='commodity_price')
    routes = x.routes(ai, p, x.derive(ai, CUTOFF))
    assert len(routes['applicable_routes']) == 2
    assert {r['economic_effect_direction'] for r in routes['applicable_routes']} == {'favourable', 'adverse'}
    assert all(r['competing_mechanisms'] and r['net_effect_status'] == 'UNKNOWN' for r in routes['applicable_routes'])


def test_exact_rate_exposure_components_can_support_conditional_route_only():
    ai = grounded(); ledger = x.derive(ai, CUTOFF)
    routes = x.routes(ai, proposition(ai), ledger)
    assert [r['route_id'] for r in routes['applicable_routes']] == ['financing_cost']
    r = routes['applicable_routes'][0]
    assert r['net_effect_status'] == 'UNKNOWN' and r['stock_effect'] is None and r['horizon'] == 'unknown'


def test_removing_contract_evidence_demotes_verified_route():
    ai = grounded(); ai['financial_facts'].pop()
    out = x.routes(ai, proposition(ai), x.derive(ai, CUTOFF))
    assert not out['applicable_routes']
    assert next(r for r in out['hypothetical_routes'] if r['route_id'] == 'financing_cost')['exposure_status'] == 'HYPOTHETICAL'


def test_total_liabilities_and_debt_maturity_do_not_establish_reset_terms():
    ai = packet(facts=[fact('liabilities'), fact('debt_due_within_one_year')])
    req = {r['requirement_id']: r for r in x.derive(ai, CUTOFF)['requirements']}
    assert req['interest_bearing_debt']['status'] == 'UNKNOWN'
    assert req['refinancing_schedule']['status'] == 'INFERRED'
    assert req['floating_rate_exposure']['status'] == 'UNKNOWN'


def test_xom_scope_is_retained_and_never_becomes_energy_sales():
    scope = 'consolidated_sales_excluding_equity_affiliate_and_other_income'
    ai = packet(facts=[fact('revenue', basis='quarter', start='2026-07-01', scope=scope)])
    ledger = x.derive(ai, CUTOFF)
    row = next(r for r in ledger['legacy_exposure']['items'] if r['id'] == 'sales_context')
    assert row['scope'] == scope
    assert next(r for r in ledger['requirements'] if r['requirement_id'] == 'energy_sales_exposure')['status'] == 'UNKNOWN'


def test_specific_activity_is_inferred_and_hedge_notional_is_not_protection():
    ai = packet(facts=[fact('energy_sales_revenue', basis='quarter', start='2026-07-01'), fact('interest_rate_hedge_notional')])
    req = {r['requirement_id']: r for r in x.derive(ai, CUTOFF)['requirements']}
    assert req['energy_sales_exposure']['status'] == 'INFERRED'
    assert req['sale_price_sensitivity']['status'] == 'UNKNOWN'


def test_conflicting_exposure_values_are_explicitly_conflicted():
    ai = grounded()
    other = fact('floating_rate_borrowings'); other.update(fact_id='other', value='101')
    ai['financial_facts'].append(other)
    req = {r['requirement_id']: r for r in x.derive(ai, CUTOFF)['requirements']}
    assert req['floating_rate_exposure']['status'] == 'CONFLICTED'
    assert e.validate(ai, CUTOFF)['evidence_verification'] == 'failed'


@pytest.mark.parametrize('key,value', [('cik', '0000000002'), ('basis', 'annual'), ('scope', 'equity_affiliate'),
                                      ('unit', 'percent'), ('value', '101'), ('end', '2026-09-29'),
                                      ('available_at', '2026-11-10T12:00:00Z'), ('fact_id', 'missing')])
def test_asserted_fact_id_identity_unit_value_period_and_scope_must_match(key, value):
    ai = grounded(); assertions = deepcopy(ai['financial_facts']); assertions[0][key] = value
    assert e.validate(ai, CUTOFF, assertions=assertions)['evidence_verification'] == 'failed'


def test_swapping_same_number_to_another_issuer_fails():
    ai = grounded(); ai['financial_facts'][0]['cik'] = '0000009999'
    assert e.validate(ai, CUTOFF)['evidence_verification'] == 'failed'


def test_duplicate_source_and_raw_source_conflict_fail():
    for mode in ['duplicate', 'conflict']:
        ai = grounded()
        if mode == 'duplicate': ai['financial_facts'][0]['source_refs'] *= 2
        else: ai['financial_facts'][1]['source_refs'][0]['raw_sha256'] = 'd' * 64
        assert e.validate(ai, CUTOFF)['evidence_verification'] == 'failed'


def test_future_availability_and_false_publication_precision_rejected():
    for mode in ['future_fact', 'invented_time', 'future_source']:
        ai = grounded()
        if mode == 'future_fact': ai['financial_facts'][0]['available_at'] = CUTOFF
        if mode == 'invented_time': ai['official_documents'][0]['published_at'] = '2026-11-08T12:00:00Z'
        if mode == 'future_source': ai['official_documents'][0].update(time_precision='second', published_at='2026-11-11T00:00:00Z')
        assert e.validate(ai, CUTOFF)['evidence_verification'] == 'failed'
