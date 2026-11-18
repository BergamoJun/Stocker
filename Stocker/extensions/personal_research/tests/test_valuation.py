"""Independent small ILLUSTRATIVE arithmetic fixtures, not live valuation gold."""
from copy import deepcopy
from decimal import Decimal
import pytest
from extensions.personal_research import valuation as v

AT='2026-11-15T12:00:00Z'
PERIOD={'start':'2025-10-01','end':'2026-09-30','basis':'TTM'}


def f(name,value,scope='consolidated',period=None,unit='USD'):
    return {'id':name,'value':str(value),'unit':unit,'scope':scope,'period':period or dict(PERIOD),'available_at':'2026-11-01T00:00:00Z'}


def inputs():
    m={'data_status':'AVAILABLE','raw_close':'100','currency':'USD','security_type':'EQUITY','security_id':'TEST_FIGI',
       'trading_date':'2026-11-13','market_cap_reference_date':'2026-11-13','provider_reported_market_cap':'1000',
       'market_cap_scope':'PROVIDER_ALL_COMMON_EQUITY_AS_CONVERTED','share_adjustment_basis':'RAW_SPLIT_BASIS_TEST'}
    instant={'start':None,'end':'2026-09-30','basis':'instant'}
    finance={'ttm_parent_net_income':f('ni',50,'parent_attributable'),'ttm_revenue':f('sales',500),'ttm_fcf':f('fcf',-10),
        'common_equity':f('equity',250,'parent_common',instant),'ttm_operating_income':f('oi',80),'ttm_depreciation_amortization':f('da',20),
        'interest_bearing_debt':f('debt',300,'interest_bearing_complete',instant),'cash':f('cash',100,period=instant),
        'ev_adjustments_complete':True}
    return m,finance


def results(m,finance):return {d['metric_name']:d for d in v.calculate(m,finance,AT)}


def test_independently_calculated_small_valuation_numbers():
    m,finance=inputs();r=results(m,finance)
    expected={'P/E':'20','P/S':'2','P/B':'4','FCF_YIELD':'-1','EV':'1200','EBITDA':'100','EV/EBITDA':'12'}
    assert {k:Decimal(r[k]['value']) for k in expected}=={k:Decimal(n) for k,n in expected.items()}
    assert all(x['formula'] and x['input_refs'] for x in r.values())


@pytest.mark.parametrize('name',['ttm_parent_net_income','common_equity'])
@pytest.mark.parametrize('number',['0','-1'])
def test_nonpositive_denominators_are_not_meaningful(name,number):
    m,finance=inputs();finance[name]['value']=number
    d=results(m,finance)['P/E' if name=='ttm_parent_net_income' else 'P/B']
    assert d['value'] is None and d['calculation_status']=='NOT_MEANINGFUL'


@pytest.mark.parametrize('change',['currency','scope','period','future','amendment','missing'])
def test_financial_alignment_blocks_only_affected_metric(change):
    m,finance=inputs();s=finance['ttm_revenue']
    if change=='currency':s['unit']='EUR'
    if change=='scope':s['scope']='segment'
    if change=='period':s['period']['basis']='annual'
    if change=='future':s['available_at']='2027-01-01T00:00:00Z'
    if change=='amendment':s['amendment_pending']=True
    if change=='missing':finance.pop('ttm_revenue')
    r=results(m,finance)
    assert r['P/S']['value'] is None and r['P/E']['value'] is not None


@pytest.mark.parametrize('change',['stale','adjusted_only','currency','reference_date','future_price','future_source'])
def test_market_misalignment_blocks_current_valuation(change):
    m,finance=inputs()
    if change=='stale':m['data_status']='STALE_DATA'
    if change=='adjusted_only':m['raw_close']=None;m['adjusted_close']='100'
    if change=='currency':m['currency']='EUR'
    if change=='reference_date':m['market_cap_reference_date']='2026-09-30'
    if change=='future_price':m['market_session_end']='2027-01-01T21:00:00Z'
    if change=='future_source':m['source_available_at']='2027-01-01T00:00:00Z'
    r=results(m,finance);assert r['P/E']['value'] is None and r['FCF_YIELD']['value'] is None


def test_total_liabilities_and_missing_da_never_become_debt_or_zero():
    m,finance=inputs();finance['total_liabilities']=finance.pop('interest_bearing_debt');finance.pop('ttm_depreciation_amortization')
    r=results(m,finance)
    assert all(r[k]['value'] is None for k in ['EV','EBITDA','EV/EBITDA']) and r['P/E']['value']=='20'


def test_eps_price_route_requires_actual_share_class_and_split_alignment():
    m,finance=inputs();finance.pop('ttm_parent_net_income')
    eps=f('eps',5,'common_diluted',unit='USD/shares');eps.update(share_adjustment_basis=m['share_adjustment_basis'],security_id=m['security_id'])
    finance['ttm_diluted_eps']=eps
    assert results(m,finance)['P/E']['value']=='20'
    eps['share_adjustment_basis']='OTHER_SPLIT_BASIS';assert results(m,finance)['P/E']['value'] is None
    eps['share_adjustment_basis']=m['share_adjustment_basis'];eps['security_id']='OTHER_CLASS';assert results(m,finance)['P/E']['value'] is None


def test_etf_cannot_inherit_company_valuation():
    m,finance=inputs();m['security_type']='ETF'
    assert all(x['value'] is None for x in results(m,finance).values())


def bridge():
    return [f('annual',1000,period={'start':'2025-01-01','end':'2025-12-31','basis':'annual'}),
            f('cytd',900,period={'start':'2026-01-01','end':'2026-09-30','basis':'cumulative_ytd'}),
            f('pytd',600,period={'start':'2025-01-01','end':'2025-09-30','basis':'cumulative_ytd'})]


def test_ttm_annual_ytd_bridge_has_exact_nonoverlapping_year():
    a,c,p=bridge();t=v.trailing(a,c,p)
    assert t['value']=='1300' and t['period']==PERIOD


@pytest.mark.parametrize('change',['gap','quarter','prior_end','currency','scope','amendment','missing'])
def test_ttm_rejects_gaps_ytd_overlap_scope_and_amendment(change):
    a,c,p=bridge()
    if change=='gap':c['period']['start']='2026-04-01'
    if change=='quarter':c['period']['basis']='quarter'
    if change=='prior_end':p['period']['end']='2025-06-30'
    if change=='currency':p['unit']='EUR'
    if change=='scope':p['scope']='parent_attributable'
    if change=='amendment':c['amendment_pending']=True
    if change=='missing':p=None
    with pytest.raises(ValueError):v.trailing(a,c,p)


def test_weighted_eps_and_share_counts_are_not_additive_ttm_money():
    a,c,p=bridge()
    for x in [a,c,p]:x['unit']='USD/shares'
    with pytest.raises(ValueError,match='ADDITIVE'):v.trailing(a,c,p)


def test_latest_annual_can_be_trailing_year_without_annualizing_a_quarter():
    a,quarter,_=bridge();t=v.annual_trailing(a)
    assert t['value']=='1000' and t['period']=={'start':'2025-01-01','end':'2025-12-31','basis':'TTM'}
    with pytest.raises(ValueError):v.annual_trailing(quarter)
