"""ILLUSTRATIVE NOT LIVE DATA; no real human review, model or market API."""
from copy import deepcopy
from datetime import date
from decimal import Decimal,localcontext,getcontext,ROUND_DOWN
from hashlib import sha256
import json
import pytest
from research.validation import stable_hash,digest
from extensions.research_v4 import seal
from extensions.financial_impact_v1 import contract,evidence,report,scenarios,energy,validator
from extensions.financial_impact_v1.examples import interest_example

AT='2026-01-02T00:00:00Z'


def fact(packet,name):
    fid=packet['exposure']['bindings'].get(name,name)
    return next(f for f in packet['facts'] if f['fact_id']==fid or f['kind']==name)


def witnesses(packet):
    """Rebuild synthetic witnesses only, never real project evidence."""
    assert packet['mode']=='ILLUSTRATIVE'
    for source in packet['sources']:
        fs=[f for f in packet['facts'] if f['source_id']==source['source_id']]
        text='ILLUSTRATIVE NOT LIVE DATA.\n'
        for f in fs:
            quote=f"{f['kind']}: {f['value']} {f['unit']}";start=len(text);text+=quote+'\n'
            f.update(exact_quote=quote,start_offset=start,end_offset=start+len(quote))
        source['text']=text;source['original_sha256']=sha256(text.encode()).hexdigest()
        for f in fs:f['original_sha256']=source['original_sha256']
    return packet


def change(packet,name,value,unit=None):
    f=fact(packet,name);f['value']=value
    if unit is not None:f['unit']=unit
    if f['kind']=='event_delta':packet['event']['delta']={'value':value,'unit':f['unit']}
    if f['kind']=='event_effective_date':packet['event']['effective_date']=value
    if name=='day_count':packet['policy']['day_count']=value
    return witnesses(packet)


def result(packet):return report.analyze(packet,created_at=AT)


def held(packet,status=None,code=None):
    r=result(packet)
    assert r['amount'] is None and r['calculation'] is None
    assert r['status']!='ARITHMETIC_VERIFIED_CONDITIONAL'
    if status:assert r['status']==status
    if code:assert code in {i['code'] for i in r['issues']}
    assert all(v is None for v in r['release'].values())
    return r


@pytest.mark.parametrize('shock,amount',[('25','2500000'),('-25','-2500000'),('0','0')])
def test_reference_hand_calculation_rate_up_down_unchanged(shock,amount):
    p=change(interest_example(),'event_delta',shock)
    r=result(p)
    assert r['status']=='ARITHMETIC_VERIFIED_CONDITIONAL' and r['amount']==amount
    assert r['calculation']['effective_days']==365 and r['calculation']['year_denominator']==365
    assert r['evidence_status']=='ILLUSTRATIVE' and r['calculation_basis']=='SCENARIO_ONLY'
    assert r['semantic_market_accuracy']=='NOT_MEASURED' and all(v is None for v in r['release'].values())


@pytest.mark.parametrize('fraction,amount',[('0.4','1000000'),('0','0')])
def test_only_repricing_fraction_is_exposed(fraction,amount):
    assert result(change(interest_example(),'repricing_fraction',fraction))['amount']==amount


def test_fixed_offset_hedge_and_hedge_missing_are_different():
    p=change(interest_example(),'hedged_fraction','0.5');change(p,'hedge_terms','FIXED_OFFSET')
    assert result(p)['amount']=='1250000'
    change(p,'hedge_terms','UNKNOWN')
    held(p,'INSUFFICIENT_EVIDENCE','HEDGE_TERMS_UNSUPPORTED')


def test_full_hedge_is_verified_zero_not_null():
    p=change(interest_example(),'hedged_fraction','1');change(p,'hedge_terms','FIXED_OFFSET')
    assert result(p)['amount']=='0'


def test_period_proportion_and_actual_360_half_year_reference():
    p=change(interest_example(),'day_count','ACT/360-v1');p['context']['calculation_end']='2026-06-30'
    r=result(p)
    assert r['calculation']['effective_days']==180 and r['amount']=='1250000'


def test_later_reset_only_applies_from_reset_date():
    p=change(interest_example(),'day_count','ACT/360-v1');p['context']['calculation_end']='2026-06-30'
    change(p,'reset_date','2026-04-01')
    r=result(p)
    assert r['amount']=='625000' and r['calculation']['effective_days']==90


def test_event_effective_date_and_reset_are_both_respected():
    p=change(interest_example(),'day_count','ACT/360-v1');p['context']['calculation_end']='2026-06-30'
    change(p,'event_effective_date','2026-04-01')
    assert result(p)['amount']=='625000'
    change(p,'reset_date','2027-01-02')
    assert result(p)['amount']=='0'


@pytest.mark.parametrize('amount,unit',[('1000000000','CURRENCY'),('1000000','THOUSAND_CURRENCY'),('1000','MILLION_CURRENCY')])
def test_exact_principal_scale_conversion(amount,unit):
    assert result(change(interest_example(),'principal',amount,unit))['amount']=='2500000'


@pytest.mark.parametrize('amount,unit',[('25','bp'),('0.25','percentage_point'),('0.0025','decimal_rate')])
def test_basis_points_percentage_points_decimal_are_same_rate(amount,unit):
    assert result(change(interest_example(),'event_delta',amount,unit))['amount']=='2500000'


def test_policy_to_coupon_mapping_must_be_explicit():
    p=change(interest_example(),'rate_mapping','0.5')
    assert result(p)['amount']=='1250000'
    fact(p,'rate_mapping')['rate_link']=None
    held(p,'INSUFFICIENT_EVIDENCE','RATE_TRANSMISSION_CONTRACT_MISSING')


def test_unrelated_rate_reference_and_currency_do_not_transmit():
    p=interest_example();p['event']['rate_reference']='DIFFERENT_BENCHMARK'
    held(p,'CONFLICTING_EVIDENCE','RATE_LINK_CONFLICT')
    p=interest_example()
    for f in p['facts']:
        if f['kind'].startswith('event_'):f['rate_currency']='EUR'
    fact(p,'rate_mapping')['rate_link']['rate_currency']='EUR';p['event']['rate_currency']='EUR'
    held(p,'INCOMPATIBLE_UNITS','RATE_CURRENCY_MISMATCH')


def test_currency_mismatch_never_gets_implicit_fx():
    p=interest_example();fact(p,'principal')['currency']='EUR'
    held(p,'INCOMPATIBLE_UNITS')


def test_incompatible_principal_unit_is_not_scaled():
    held(change(interest_example(),'principal','1000000000','shares'),'INCOMPATIBLE_UNITS')


def test_missing_contract_does_not_fall_back_to_liabilities():
    p=interest_example();p['exposure']['bindings']['principal']=None
    p['context_facts']=[{'metric':'total_liabilities','value':'1000000000','is_illustrative':True}]
    held(p,'INSUFFICIENT_EVIDENCE','MISSING_PRINCIPAL')


def test_other_issuer_source_or_fact_is_rejected():
    p=interest_example();fact(p,'principal')['cik']='0000000002'
    held(p,'CONFLICTING_EVIDENCE','ISSUER_MISMATCH')
    p=interest_example();p['sources'][1]['issuer']['ticker']='OTHER'
    held(p,'CONFLICTING_EVIDENCE')


def test_same_issuer_different_loan_cannot_supply_reset_or_hedge():
    p=interest_example();fact(p,'reset_date')['instrument_id']='different_loan'
    held(p,'CONFLICTING_EVIDENCE','INSTRUMENT_MISMATCH')


def test_clamped_python_slice_cannot_certify_out_of_range_end_offset():
    p=interest_example();f=p['facts'][-1];f['end_offset']+=100000
    held(p,'CONFLICTING_EVIDENCE','EXACT_WITNESS_MISMATCH')


def test_conflicting_event_and_exposure_are_not_just_missing():
    p=interest_example();p['event']['verification_status']='CONFLICTING'
    held(p,'CONFLICTING_EVIDENCE','CONFLICTING_EVENT')


def test_report_period_and_accounting_scope_conflicts():
    p=interest_example();fact(p,'principal')['reporting_period']['end']='2024-12-31'
    held(p,'INVALID_PERIOD','REPORTING_PERIOD_MISMATCH')
    p=interest_example();fact(p,'principal')['accounting_scope']='PARENT_ONLY'
    held(p,'CONFLICTING_EVIDENCE','ACCOUNTING_SCOPE_MISMATCH')


def test_raw_source_hash_and_exact_offsets_are_independently_checked():
    p=interest_example();p['sources'][1]['text']+='changed'
    held(p,'CONFLICTING_EVIDENCE','SOURCE_HASH_CHANGED')
    p=interest_example();fact(p,'principal')['start_offset']+=1
    held(p,'CONFLICTING_EVIDENCE','EXACT_WITNESS_MISMATCH')


@pytest.mark.parametrize('target',['published_at','available_at'])
def test_future_source_information(target):
    p=interest_example();p['sources'][0][target]='2026-01-02T00:00:00Z'
    held(p,code='FUTURE_INFORMATION')


def test_equal_cutoff_fact_and_later_review_are_not_available():
    p=interest_example();fact(p,'principal')['available_at']=p['context']['as_of']
    held(p,'INSUFFICIENT_EVIDENCE','FUTURE_FACT')


def test_staleness_and_contract_window_fail_closed():
    p=interest_example();p['policy']['max_source_age_days']=0
    held(p,'STALE_EVIDENCE','SOURCE_STALE')
    p=interest_example();fact(p,'principal')['valid_to']='2026-06-30'
    held(p,'INVALID_PERIOD','CONTRACT_WINDOW_MISMATCH')


def test_duplicate_canonical_event_overlap_ignores_alias_ids():
    p=interest_example();p['prior_calculations']=[{'cik':p['context']['cik'],
        'canonical_event_id':p['event']['canonical_event_id'],'instrument_id':p['exposure']['instrument_id'],
        'start':'2026-06-01','end':'2026-07-01','scenario_fingerprint':validator.scenario_fingerprint(p)}]
    p['event']['event_id']='renamed_event'
    held(p,'CALCULATION_FAILED','DUPLICATE_OR_OVERLAPPING_CALCULATION')


def test_duplicate_or_conflicting_component_facts():
    p=interest_example();p['facts'].append(deepcopy(fact(p,'principal')))
    held(witnesses(p),'CONFLICTING_EVIDENCE','DUPLICATE_FACT_ID')
    p=interest_example();extra=deepcopy(fact(p,'principal'));extra.update(fact_id='conflict_principal',value='2000000000');p['facts'].append(extra)
    held(witnesses(p),'CONFLICTING_EVIDENCE','CONFLICTING_COMPONENT_FACTS')


def test_decimal_calculation_does_not_depend_on_global_context():
    p=interest_example();p['context']['calculation_end']='2026-04-01';expected=result(p)
    with localcontext() as ctx:
        ctx.prec=5;ctx.rounding=ROUND_DOWN
        assert result(p)==expected
    with localcontext() as ctx:
        ctx.prec=50
        assert Decimal(expected['amount'])==Decimal(2500000)*90/365


def test_scenarios_are_independent_snapshots_and_keep_original_event_evidence():
    p=interest_example();before=deepcopy(p);rows=scenarios.standard(p,created_at=AT)
    assert p==before
    assert [r['output']['amount'] for r in rows]==['0','-2500000','2500000']
    assert all(r['input']['event']==p['event'] and r['input']['assumptions']['probability'] is None for r in rows)
    rows[0]['input']['facts'][0]['value']='changed'
    assert rows[1]['input']['facts'][0]['value']=='25' and p==before


def test_unverified_event_is_not_rescued_by_scenario_override():
    p=interest_example();p['event']['verification_status']='UNVERIFIED'
    for r in scenarios.standard(p,created_at=AT):assert r['output']['amount'] is None


@pytest.mark.parametrize('field,value',[('repricing_fraction','1.1'),('principal','-1'),('hedged_fraction','-0.1'),('rate_mapping','NaN')])
def test_bad_numeric_domains(field,value):
    held(change(interest_example(),field,value),'CALCULATION_FAILED')


def test_caps_amortization_and_capitalization_are_not_silently_ignored():
    p=change(interest_example(),'event_delta','101');held(p,'INSUFFICIENT_EVIDENCE','OUTSIDE_LINEAR_CONTRACT_RANGE')
    held(change(interest_example(),'principal_schedule','AMORTIZING'),'INSUFFICIENT_EVIDENCE','AMORTIZATION_UNSUPPORTED')
    held(change(interest_example(),'accounting_treatment','CAPITALIZED'),'INSUFFICIENT_EVIDENCE','ACCOUNTING_TREATMENT_UNSUPPORTED')


def test_floats_extra_fields_and_probability_fail_closed_schema():
    p=interest_example();fact(p,'principal')['value']=1e9;held(p,'CALCULATION_FAILED','INPUT_CONTRACT_INVALID')
    p=interest_example();p['policy']['default_principal']='1';held(p,'CALCULATION_FAILED')
    p=interest_example();p['assumptions']['probability']='0.5';held(p,'CALCULATION_FAILED')


def test_actual_mode_cannot_adopt_synthetic_facts_or_self_assigned_verified():
    p=interest_example();p['mode']='REAL'
    held(p,'CONFLICTING_EVIDENCE','REAL_ILLUSTRATIVE_MIX')
    # An adversarial fixture emulates false VERIFIED labels. It is NOT human evidence.
    for source in p['sources']:source['is_illustrative']=False
    for f in p['facts']:f['verification_status']='VERIFIED'
    p['event']['verification_status']=p['exposure']['verification_status']='VERIFIED'
    held(p,'INSUFFICIENT_EVIDENCE','EXTERNAL_REVIEW_MISSING')


def test_owner_allowlist_does_not_authenticate_or_generate_human_reviews():
    p=interest_example();p['mode']='REAL'
    for s in p['sources']:s['is_illustrative']=False
    for f in p['facts']:
        f['verification_status']='VERIFIED'
        # Explicit fake attestation for rejection tests only; never independent gold.
        f['review']=seal({'reviewer_id':'ILLUSTRATIVE_FAKE_REVIEWER','reviewed_at':'2025-12-31T12:00:00Z',
            'source_sha256':f['original_sha256'],'fact_sha256':evidence.fact_hash(f)})
    p['event']['verification_status']=p['exposure']['verification_status']='VERIFIED'
    held(p,'INSUFFICIENT_EVIDENCE','REVIEW_NOT_OWNER_TRUSTED')
    p['policy']['trusted_review_hashes']=[f['review']['record_sha256'] for f in p['facts']]
    fact(p,'principal')['review']['fact_sha256']='0'*64
    held(p,'CONFLICTING_EVIDENCE','REVIEW_BINDING_CHANGED')


def test_output_tampering_resealed_is_detected_by_recomputation():
    p=interest_example();r=result(p);r['amount']='999';r=seal({k:v for k,v in r.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='REPLAY_CHANGED'):report.replay(p,r)


def test_immutable_report_bundle_and_render_tamper_detection(tmp_path):
    p=interest_example();before=deepcopy(p);folder=report.write(p,tmp_path,created_at=AT)
    assert p==before and report.validate(folder)['status']=='VALID'
    second=report.write(p,tmp_path,created_at=AT);assert second!=folder
    (folder/'REPORT.ko.md').write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='FILES_CHANGED'):report.validate(folder)


def test_invalid_period_unsupported_event_and_created_at_cutoff():
    p=interest_example();p['context']['calculation_end']=p['context']['calculation_start'];held(p,'INVALID_PERIOD')
    p=interest_example();p['event']['event_type']='STOCK_PRICE_MOVE';held(p,'UNSUPPORTED_EVENT')
    r=report.analyze(interest_example(),created_at='2025-12-01T00:00:00Z')
    assert r['status']=='INSUFFICIENT_EVIDENCE' and r['amount'] is None


def test_day_count_is_required_and_must_match_contract():
    p=interest_example();p['policy']['day_count']=None;held(p,code='DAY_COUNT_MISSING')
    p=interest_example();p['policy']['day_count']='ACT/360-v1';held(p,'INVALID_PERIOD','DAY_COUNT_CONTRACT_MISMATCH')


def test_legacy_adapter_uses_original_validator_without_modifying_report(tmp_path,monkeypatch):
    from extensions import research_v3_report_v2 as old
    p=interest_example();folder=tmp_path/'old';folder.mkdir()
    (folder/'RESEARCH_V3_1.json').write_text('{}');(folder/'FILES.json').write_text('{}')
    seen=[]
    def validate(path):seen.append(path);return {'identity':{'cik':p['context']['cik'],'symbol':p['context']['ticker']},'evidence_as_of':p['context']['as_of']}
    monkeypatch.setattr(old,'validate',validate)
    before={n.name:digest(n) for n in folder.iterdir()}
    r=report.legacy_reference(folder,p)
    assert r['contract']=='issuer-research-report-v3.1' and seen==[folder]
    assert before=={n.name:digest(n) for n in folder.iterdir()}


def test_energy_contract_interface_defers_amount_even_when_values_exist():
    v={'contract':'financial-impact-energy-interface-v1','context':interest_example()['context'],
        'path':'ENERGY_PROCUREMENT_COST','event_id':'demo_energy','energy_type':'electricity',
        'quantity':'100','quantity_unit':'MWh','price_delta':'2','price_unit':'USD/MWh',
        'contract_price_index':None,'hedge_terms':None,'pass_through_fraction':None,
        'source_id':None,'fact_ids':[],'original_sha256':None,'published_at':None,'available_at':None,
        'verification_status':'UNVERIFIED'}
    r=energy.inspect(v);assert r['amount'] is None and r['status']=='INSUFFICIENT_EVIDENCE'
    v['price_unit']='USD/barrel';assert energy.inspect(v)['status']=='INCOMPATIBLE_UNITS'


def test_leap_year_fixed_denominator_is_not_actual_actual():
    p=interest_example();p['context'].update(calculation_start='2028-01-01',calculation_end='2029-01-01')
    for f in p['facts']:
        if not f['kind'].startswith('event_'):f['valid_from']='2028-01-01';f['valid_to']='2029-01-01'
    r=result(p);assert r['calculation']['effective_days']==366 and r['calculation']['year_denominator']==365
    with localcontext() as ctx:
        ctx.prec=50;assert Decimal(r['amount'])==Decimal(2500000)*366/365


def test_invalid_reset_date_and_missing_day_count_are_distinct():
    held(change(interest_example(),'reset_date','not-a-date'),'INVALID_PERIOD','RESET_DATE_INVALID')
    p=interest_example();p['policy']['day_count']=None;held(p,'INSUFFICIENT_EVIDENCE','DAY_COUNT_MISSING')


def test_emitted_ledger_record_blocks_repeated_same_scenario():
    p=interest_example();r=result(p);p['prior_calculations']=[r['calculation']['ledger_record']]
    held(p,'CALCULATION_FAILED','DUPLICATE_OR_OVERLAPPING_CALCULATION')
