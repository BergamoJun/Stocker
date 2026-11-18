"""ILLUSTRATIVE — NOT LIVE MARKET DATA; independent hand-computed expectations."""
from copy import deepcopy
from decimal import Decimal
import pytest
from extensions.personal_research import scenarios as s,relative_valuation as r,historical_valuation as h
from extensions.personal_research.tests.test_valuation import inputs,AT


def base():
    return {'issuer':'TEST','ticker':'TEST','currency':'USD','security_id':'FIGI_TEST','reference_revenue':'100',
        'reference_market_cap':'300','reference_net_income':'20','reference_operating_income':'25','reference_fcf':'-5',
        'current_pe':'15','current_ps':'3','reference_period':{'start':'2025-01-01','end':'2025-12-31','basis':'TTM'},
        'reference_period_classification':'VERIFIED_TTM','reference_period_days':365,'price_date':'2026-11-13',
        'source_as_of':AT,'input_refs':['ILLUSTRATIVE:revenue','ILLUSTRATIVE:cap'],'share_adjustment_basis':'RAW_TEST'}


def assumption(**changes):
    values={'growth':'.1','net_margin':'.2','operating_margin':'.25','fcf_margin':'-.05','exit_pe':'15','exit_ps':'3'};values.update(changes)
    return {'scenario_name':'base','horizon_years':1,'fields':{k:s.assumption(k,v,'USER_ASSUMPTION','ILLUSTRATIVE fixture',[],'Hand calculated software control') for k,v in values.items()}}


def test_exact_one_year_earnings_sales_fcf_and_equity_value():
    b=base();a=assumption();old=deepcopy((b,a));out=s.calculate(b,a)
    assert Decimal(out['projected_revenue'])==110 and Decimal(out['projected_net_income'])==22
    assert Decimal(out['projected_operating_income'])==Decimal('27.5') and Decimal(out['projected_fcf'])==Decimal('-5.5')
    assert all(Decimal(x['equity_value'])==330 and Decimal(x['change_vs_reference_market_cap_percent'])==10 for x in out['valuations'])
    assert all(x['per_share_value'] is None and x['enterprise_value'] is None for x in out['valuations'])
    assert (b,a)==old


def test_three_year_compounding_is_not_three_times_one_year_growth():
    a=assumption();a['horizon_years']=3;out=s.calculate(base(),a)
    assert Decimal(out['projected_revenue'])==Decimal('133.1')
    assert Decimal(out['valuations'][0]['equity_value'])==Decimal('399.3')


@pytest.mark.parametrize('growth,margin,expected',[('-.5','.2','150'),('0','.2','300'),('-.1','.1','135')])
def test_negative_and_zero_growth_are_economic(growth,margin,expected):
    out=s.calculate(base(),assumption(growth=growth,net_margin=margin))
    assert Decimal(out['valuations'][0]['equity_value'])==Decimal(expected)


@pytest.mark.parametrize('margin',['0','-.2'])
def test_nonpositive_net_income_blocks_pe_but_not_sales(margin):
    out=s.calculate(base(),assumption(net_margin=margin))
    assert out['valuations'][0]['equity_value'] is None and out['valuations'][1]['equity_value'] is not None


@pytest.mark.parametrize('key,value',[('growth','-1.01'),('growth','2.01'),('net_margin','1.1'),('exit_pe','0'),('exit_ps','-1')])
def test_non_economic_assumptions_raise(key,value):
    with pytest.raises(ValueError):s.calculate(base(),assumption(**{key:value}))


@pytest.mark.parametrize('value',[True,None,'NaN','Infinity','not-a-number'])
def test_finite_decimal_input(value):
    with pytest.raises(ValueError):s.decimal(value)


@pytest.mark.parametrize('value',[True,0,2,5,'1'])
def test_only_explicit_one_or_three_year_horizons(value):
    with pytest.raises(ValueError):s.horizon(value)


def test_currency_mismatch_no_conversion_and_no_ev_equity_substitution():
    b=base();b['currency']='EUR'
    with pytest.raises(ValueError):s.calculate(b,assumption())
    assert s.calculate(base(),assumption())['valuations'][0]['enterprise_value'] is None


def shares():
    return {'scope':'same_equity_value_per_security','count_basis':'current_outstanding','security_id':'FIGI_TEST',
        'currency':'USD','split_basis':'RAW_TEST','date':'2026-11-13','available_at':'2026-11-14T00:00:00Z',
        'value':'10','annual_dilution_assumption':'.1'}


def test_per_share_requires_explicit_future_dilution():
    x=shares();out=s.calculate(base(),assumption(),x)['valuations'][0]
    assert Decimal(out['projected_share_count'])==11 and Decimal(out['per_share_value'])==30
    x.pop('annual_dilution_assumption')
    assert s.calculate(base(),assumption(),x)['valuations'][0]['per_share_value'] is None


@pytest.mark.parametrize('key,value',[('scope','single_class_only'),('count_basis','EPS_weighted_average'),('security_id','OTHER_CLASS'),('split_basis','ADJUSTED'),('currency','EUR'),('date','2020-01-01'),('available_at','2099-01-01T00:00:00Z')])
def test_invalid_share_basis_stays_null(key,value):
    x=shares();x[key]=value
    assert s.calculate(base(),assumption(),x)['valuations'][0]['per_share_value'] is None


@pytest.mark.parametrize('method',['EARNINGS','SALES'])
def test_three_by_three_grid_center_monotonicity_and_no_mutation(method):
    a=assumption();original=deepcopy(a);grid=s.sensitivity(base(),a,method)
    assert len(grid['grid'])==3 and all(len(row)==3 for row in grid['grid'])
    assert Decimal(grid['grid'][1][1])==330
    assert all(Decimal(row[0])<Decimal(row[1])<Decimal(row[2]) for row in grid['grid'])
    assert Decimal(grid['grid'][0][1])<Decimal(grid['grid'][1][1])<Decimal(grid['grid'][2][1])
    assert a==original and grid==s.sensitivity(base(),a,method)


def test_sensitivity_negative_profit_nulls_not_zero_and_invalid_size():
    g=s.sensitivity(base(),assumption(net_margin='-.1'),'EARNINGS');assert all(x is None for row in g['grid'] for x in row)
    with pytest.raises(ValueError):s.sensitivity(base(),assumption(),'SALES',([0,1],[1,2]))


def value(ticker='TEST',cik='1'):
    from extensions.personal_research import valuation
    market,finance=inputs();market.update(cik=cik,security_id='FIGI_'+ticker)
    return {'ticker':ticker,'issuer':{'cik':cik},'analysis_as_of':AT,'market_snapshot':market,'valuation_financials':finance,
        'valuation_metrics':valuation.calculate(market,finance,AT),'business_profile':[],'source_references':{}}


def candidates(n):return [{'comparability_status':'PARTIALLY_COMPARABLE'} for _ in range(n)]


def test_peer_median_even_odd_and_exact_premium():
    assert r.median(['3','1','2'])==2 and r.median(['1','2','3','4'])==Decimal('2.5')
    target=value();a=value('A','2');b=value('B','3')
    for v,pe in [(target,'25'),(a,'18'),(b,'22')]:next(x for x in v['valuation_metrics'] if x['metric_name']=='P/E')['value']=pe
    out=next(x for x in r.compare(target,[a,b],candidates(2)) if x['metric_name']=='P/E')
    assert out['peer_median']=='20' and Decimal(out['premium_discount_percent'])==25
    assert out['output_status']=='PARTIAL_COMPARISON'


def test_one_peer_is_not_industry_median_and_duplicates_do_not_inflate_coverage():
    v=value();a=value('A','2');out=r.compare(v,[a],candidates(1))[0]
    assert out['peer_median'] is None and out['output_status']=='INSUFFICIENT_PEERS'
    assert r.compare(v,[a,deepcopy(a)],candidates(2))[0]['eligible_peer_count']==1


@pytest.mark.parametrize('change',['currency','date','fiscal53','class','scope','negative','zero','extreme','nearzero','future','definition'])
def test_peer_guards(change):
    t=value();p=value('PEER','2');m=p['market_snapshot'];pe=next(x for x in p['valuation_metrics'] if x['metric_name']=='P/E')
    if change=='currency':m['currency']='EUR'
    if change=='date':m['trading_date']='2026-11-12'
    if change=='fiscal53':pe['fiscal_period']={'start':'2025-08-29','end':'2026-09-03','basis':'latest_fiscal_year'}
    if change=='class':m['cik']='OTHER_CIK'
    if change=='scope':m['market_cap_scope']='ONE_CLASS_ONLY'
    if change=='negative':pe['value']='-5'
    if change=='zero':pe['value']='0'
    if change=='extreme':pe['value']='501'
    if change=='nearzero':p['valuation_financials']['ttm_parent_net_income']['value']='.1'
    if change=='future':p['analysis_as_of']='2099-01-01T00:00:00Z'
    if change=='definition':pe['formula']='price/basic_EPS'
    assert r.compatible(t,p,'P/E')


def test_fcf_yield_is_percentage_point_spread_and_negative_yields_are_allowed():
    t=value();a=value('A','2');b=value('B','3')
    for v,x in [(t,'-1'),(a,'-3'),(b,'-1')]:next(y for y in v['valuation_metrics'] if y['metric_name']=='FCF_YIELD')['value']=x
    out=next(x for x in r.compare(t,[a,b],candidates(2)) if x['metric_name']=='FCF_YIELD')
    assert Decimal(out['yield_spread_percentage_points'])==1 and out['premium_discount_percent'] is None


def test_industry_label_cannot_approve_economically_unproven_peers():
    result=r.compare(value(),[value('A','2'),value('B','3')],[{'comparability_status':'INDUSTRY_REFERENCE_ONLY'}]*2)
    assert all(x['peer_median'] is None for x in result)
    assert all(x['equity_value'] is None for x in s.calculate(base(),assumption(net_margin='-.1'))['valuations'] if x['method']=='EARNINGS')


def current():
    return {'value':'20','security_id':'FIGI_TEST','issuer':'TEST','currency':'USD','share_basis':'RAW_AS_TRADED',
        'formula':'market_cap/TTM_parent_net_income','period_classification':'VERIFIED_TTM'}


def historical():
    points=[]
    for day,val in [('2026-01-05','10'),('2026-01-06','20'),('2026-01-07','30')]:
        at=day+'T23:00:00Z';point={**current(),'value':val,'trading_date':day,'historical_as_of':at,
            'market':{'available_at':day+'T22:00:00Z','retrieved_at':day+'T22:00:00Z','session_end':day+'T21:00:00Z','point_in_time_verified':True,'market_cap_date':day,'shares_date':day},
            'financial':{'accepted_at':'2026-01-01T12:00:00Z','available_at':'2026-01-01T12:00:00Z','retrieved_at':'2026-01-01T12:00:00Z'}}
        points.append(point)
    return points


def test_independent_small_pit_percentile_and_reconstruction_not_pit():
    out=h.evaluate(historical(),current(),AT)
    assert out['output_status']=='AVAILABLE' and abs(Decimal(out['percentile'])-Decimal(200)/3)<Decimal('1e-25')
    points=historical()
    for p in points:p['market']['point_in_time_verified']=False
    out=h.evaluate(points,current(),AT);assert out['percentile'] is None and out['classification']=='RECONSTRUCTED_HISTORICAL_REFERENCE'


@pytest.mark.parametrize('change',['financial_future','retrieved_future','price_future','split','ticker','issuer','cap_vintage','shares_vintage','revised','intraday'])
def test_historical_vintage_guards(change):
    points=historical()
    for p in points:
        if change=='financial_future':p['financial']['accepted_at']='2026-02-01T00:00:00Z'
        if change=='retrieved_future':p['market']['retrieved_at']='2026-11-01T00:00:00Z'
        if change=='price_future':p['historical_as_of']='2026-01-01T00:00:00Z'
        if change=='split':p['share_basis']='DIVIDEND_ADJUSTED'
        if change=='ticker':p['security_id']='REUSED_TICKER_OTHER_FIGI'
        if change=='issuer':p['issuer']='PREDECESSOR'
        if change=='cap_vintage':p['market']['market_cap_date']='2026-11-13'
        if change=='shares_vintage':p['market']['shares_date']='2026-11-13'
        if change=='revised':p['financial']['revised_after_cutoff']=True
        if change=='intraday':p['market']['session_end']=p['trading_date']+'T23:30:00Z'
    assert h.evaluate(points,current(),AT)['percentile'] is None


def test_observed_and_future_assumptions_are_separate_and_company_specific():
    v={'ticker':'MSFT','business_segments':[],'derived_metrics':[]};b=base()
    ms=s.defaults(v,b);mu=s.defaults({**v,'ticker':'MU'},b)
    assert ms[1]['fields']['net_margin']['assumption_type']=='ILLUSTRATIVE_ASSUMPTION'
    assert Decimal(mu[1]['fields']['net_margin']['assumption_value'])==Decimal('.1')
    assert mu[1]['fields']['net_margin']['assumption_value']!=ms[1]['fields']['net_margin']['assumption_value']
    custom=s.overrides(ms,{'base':{'growth':'.3'}});assert custom[1]['fields']['growth']['assumption_type']=='USER_ASSUMPTION'
    assert ms[1]['fields']['growth']['assumption_value']!='.3'


@pytest.mark.parametrize('ticker,conflict',[('ORCL',False),('ORCL',True),('AMZN',False)])
def test_peer_statement_mapping_uses_existing_bridge_and_refuses_conflict(monkeypatch,ticker,conflict):
    from extensions.personal_research import coverage
    from extensions.personal_research.tests.test_intelligence import numeric
    from extensions.personal_research.tests.test_valuation import f
    annual={'form':'10-K','report_date':'2026-06-30','accession':'A','filed_date':'2026-08-01'}
    quarter={'form':'10-Q','report_date':'2026-09-30','accession':'Q','filed_date':'2026-11-01'}
    source={'source_url':'https://example.invalid/ILLUSTRATIVE','raw_path':'ILLUSTRATIVE','raw_sha256':'HASH','available_at':'2026-11-02T00:00:00Z'}
    tag='us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax' if ticker=='ORCL' else 'us-gaap:PaymentsToAcquireProductiveAssets'
    row='Total revenues ' if ticker=='ORCL' else 'Purchases of property and equipment '
    a=numeric(tag,100,'2025-07-01','2026-06-30',1);a['row_text']=row+'100'
    c=numeric(tag,50,'2026-07-01','2026-09-30',2);c['row_text']=row+'50'
    p=numeric(tag,25,'2025-07-01','2025-09-30',3);p['row_text']=row+'25'
    ar=[a]
    if ticker=='ORCL':ar.append({**a,'tag':'us-gaap:Revenues','value':'101' if conflict else '100','ordinal':4})
    monkeypatch.setattr(coverage,'documents',lambda *args:iter([(annual,'A',source,b'',ar,{'c':[]}), (quarter,'Q',source,b'',[c,p],{'c':[]})]))
    v=value(ticker,'2');v['valuation_financials']['ttm_operating_cash_flow']=f('ocf',200)
    old=deepcopy(v['valuation_financials']);snap={'filings':[annual,quarter],'available_at':source['available_at']}
    out=r.peer_extension(v,snap)
    if conflict:assert out['valuation_financials']==old;return
    field='ttm_revenue' if ticker=='ORCL' else 'ttm_capital_expenditure'
    assert Decimal(out['valuation_financials'][field]['value'])==125
    if ticker=='AMZN':assert Decimal(out['valuation_financials']['ttm_fcf']['value'])==75
    assert out['peer_mapping_notes'] and out['source_references']
