"""ILLUSTRATIVE - no live values or network/model calls."""
from copy import deepcopy
import warnings
from pathlib import Path
import pytest
from extensions import issuer_impact as impact, impact_quality as quality


def sample(): return deepcopy(quality.cases()[0]['input'])


@pytest.mark.parametrize('case',[c for c in quality.cases() if c['safety']],ids=lambda c:c['id'])
def test_unapproved_inputs_block_before_model(case):
    with pytest.raises((ValueError,KeyError)): impact.validate_input(case['input'])


@pytest.mark.parametrize('change',['unit','scope','cik','basis','available_at','end','value','extra'])
def test_bad_fact_never_supplies_candidate(change):
    v=sample(); f=v['facts'][0]
    bad={'unit':'USD/shares','scope':'segment','cik':'0000000002','basis':'quarter','available_at':v['as_of'],'end':'2020-01-01','value':'NaN'}
    if change=='extra': f['vendor_price']='ILLUSTRATIVE'
    else: f[change]=bad[change]
    with pytest.raises(ValueError): impact.candidates(v)


def test_liabilities_only_conditional_not_verified_debt():
    route=next(r for r in impact.candidates(sample()) if r['route_id']=='financing_cost')
    assert route['exposure_verified'] is False
    assert route['assumption_id']=='interest_bearing_rate_sensitive_exposure_unverified'
    assert route['claimed_horizon']=='unknown'


def test_conflict_withheld_not_latest_receipt_winner():
    v=sample(); a=deepcopy(v['facts'][0]); b=deepcopy(a); b.update(fact_id='conflict',value='999')
    for f in [a,b]: f['validation_kind']='recorded_source_review'
    facts,gaps=impact.select_facts({'identity':v['identity'],'financial_facts':[a,b]},v['as_of'])
    assert not facts and 'cash:conflict' in gaps


def test_equal_values_distinct_periods_not_merged():
    v=sample(); a=deepcopy(v['facts'][-1]); b=deepcopy(a); b.update(fact_id='annual',basis='annual',start='2026-01-01')
    for f in [a,b]: f['validation_kind']='deterministic_source_match'
    facts,_=impact.select_facts({'identity':v['identity'],'financial_facts':[a,b]},v['as_of'])
    assert len(facts)==1 and facts[0]['basis']=='quarter'


def test_date_only_boundary_is_not_exact_seven_day_match():
    d=sample()['source']; d['published_date']='2026-11-02'
    assert not impact.source_window(d,'2026-11-09T14:00:00Z',7)
    d['published_date']='2026-11-03'
    assert impact.source_window(d,'2026-11-09T14:00:00Z',7)


def test_no_direction_or_zero_facts_does_not_create_signal():
    v=sample(); v['classification']['variable_direction']='unchanged'
    assert impact.candidates(v)==[]
    v=sample()
    for f in v['facts']: f['value']='0'
    assert impact.candidates(v)==[]


def test_prompt_contains_only_safe_contract_and_cites_bound_quote():
    v=sample(); seen=[]
    class Backend:
        def ask(self,msg,schema):
            seen.extend(msg)
            return {'route_id':'financing_cost','quote':v['classification']['quote']},{}
    proposal,_=impact.research(v,Backend())
    assert proposal['fact_id']=='illustrative_liabilities'
    assert all('market_price' not in m['content'] and 'api_key' not in m['content'] for m in seen)


def test_new_sources_compile_without_syntax_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter('error',SyntaxWarning)
        for name in ['issuer_impact.py','impact_critic.py','impact_report.py','impact_quality.py']:
            p=impact.BASE/name; compile(p.read_text(encoding='utf-8'),str(p),'exec')
