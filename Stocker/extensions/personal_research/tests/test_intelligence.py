"""ILLUSTRATIVE — NOT LIVE MARKET DATA; authored checks, not independent gold."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from extensions.personal_research import business,segments,coverage,core,report
from extensions.personal_research.tests.test_personal import stored


def test_sections_visible_paragraph_and_exact_original_span():
    raw=b'<html><div>ITEM 1. Business</div><p>We supply DRAM memory products to data center customers and sell NAND products.</p><div>ITEM 1A. Risk Factors</div><p>Our DRAM selling prices are cyclical and supply constraints may adversely affect our manufacturing operations.</p><div>ITEM 7. Management Discussion</div><p>Capital expenditures expanded our manufacturing capacity during the fiscal year.</p></html>'
    rows=business.paragraphs(raw,'10-K')
    p=next(x for x in rows if 'We supply' in x['text'])
    assert p['section']=='BUSINESS'
    assert raw.decode()[p['char_start']:p['char_end']]=='<p>'+p['text']+'</p>'
    assert next(x for x in rows if 'cyclical' in x['text'])['section']=='RISK_FACTORS'
    assert rows[-1]['section']=='MD&A'


def test_hidden_script_and_nested_markup_are_not_business_evidence():
    raw=b'<div style="display:none"><p>fabricated cloud advantage</p></div><script>fake memory</script><div>ITEM 1. Business</div><p>Visible <b>cloud</b> subscription products.</p>'
    text=[x['text'] for x in business.paragraphs(raw,'10-K')]
    assert 'Visible cloud subscription products.' in text
    assert all('fabricated' not in x and 'fake memory' not in x for x in text)


def test_10q_item1_is_not_annual_business():
    rows=business.paragraphs(b'<p>Item 1. Financial Statements</p><p>Some facts.</p><p>Item 2. Management Discussion</p><p>cloud consumption grew.</p>','10-Q')
    assert rows[1]['section']=='FINANCIAL_STATEMENTS' and rows[-1]['section']=='MD&A'


@pytest.mark.parametrize('dimensions',[
    [('explicitmember','srt:StatementGeographicalAxis','country:US')],
    [('explicitmember','srt:ProductOrServiceAxis','test:DRAMMember')],
    [('explicitmember','us-gaap:StatementBusinessSegmentsAxis','test:A'),('explicitmember','srt:StatementGeographicalAxis','country:US')],
    [('typedmember','us-gaap:StatementBusinessSegmentsAxis','test:A')],
    [('explicitmember','us-gaap:StatementBusinessSegmentsAxis','test:A'),('explicitmember','us-gaap:StatementBusinessSegmentsAxis','test:B')]])
def test_segments_never_accept_product_geography_or_ambiguous_dimensions(dimensions):
    assert segments.member(dimensions) is None


def test_segment_operating_consolidation_member_is_accepted_in_both_container_types():
    for container in [list,tuple]:
        assert segments.member([container(['explicitmember','us-gaap:StatementBusinessSegmentsAxis','test:CloudMember']),container(['explicitmember','srt:ConsolidationItemsAxis','us-gaap:OperatingSegmentsMember'])])=='test:CloudMember'


def numeric(tag,value,start='2026-01-01',end='2026-12-31',ordinal=1):
    return {'tag':tag,'value':str(value),'start':start,'end':end,'ordinal':ordinal,'context_ref':'c',
        'is_instant':start is None,'unit':'USD','same_entity':True,'hidden':False,'status':'parsed_numeric_evidence_only','row_text':'','dom_id':None,'row_number':1}


@pytest.mark.parametrize('change',['currency','issuer','hidden','period','conflict'])
def test_scoped_financial_pick_fails_on_wrong_evidence(change):
    x=numeric('us-gaap:StockholdersEquity',100,None);rows=[x]
    if change=='currency':x['unit']='EUR'
    if change=='issuer':x['same_entity']=False
    if change=='hidden':x['hidden']=True
    if change=='period':x['end']='2025-12-31'
    if change=='conflict':rows.append({**x,'value':'200','ordinal':2})
    assert coverage.exact(rows,[x['tag']],None,'2026-12-31',instant=True) is None


def test_common_equity_requires_actual_common_row_not_total_equity(monkeypatch):
    f={'form':'10-Q','accession':'TEST','report_date':'2026-12-31','filed_date':'2027-01-01'}
    s={'source_url':'https://example.invalid','raw_path':'ILLUSTRATIVE','raw_sha256':'x','available_at':'2027-01-01T00:00:00Z'}
    x=numeric('us-gaap:StockholdersEquity',-10,None);x['row_text']='Total stockholders equity attributable to common stockholders'
    snapshot={'cik':'0000001001','available_at':s['available_at'],'filings':[]}
    monkeypatch.setattr(coverage.valuation,'financials',lambda *a:({}, {}, []))
    monkeypatch.setattr(coverage,'documents',lambda *a:iter([(f,'test',s,b'', [x],{'c':[]})]))
    finance,refs,issues,components=coverage.financials(snapshot,'2027-01-02T00:00:00Z')
    assert finance['common_equity']['value']=='-10' and finance['common_equity']['scope']=='parent_common'
    x['row_text']='Total equity including noncontrolling interests'
    assert 'common_equity' not in coverage.financials(snapshot,'2027-01-02T00:00:00Z')[0]


def test_be_revenue_is_scoped_to_displayed_total_not_customer_subtotal():
    total=numeric('us-gaap:Revenues',110);total['row_text']='Total revenue 110'
    customer=numeric('us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',100)
    chosen=coverage.exact([total,customer],['us-gaap:Revenues'],'2026-01-01','2026-12-31',row=lambda s:s.startswith('total revenue '))
    assert chosen['value']=='110'
    # The two different accounting concepts are never added together.
    assert chosen['value']!='210'


def test_latest_53week_year_keeps_original_amount_and_explicit_label(monkeypatch):
    x={'id':'year','value':'371','unit':'USD','scope':'consolidated','period':{'start':'2025-08-29','end':'2026-09-03','basis':'TTM'},
       'formula':'latest_disclosed_annual_period_is_complete_trailing_fiscal_year'}
    monkeypatch.setattr(coverage.valuation,'financials',lambda *a:({'ttm_revenue':deepcopy(x)}, {}, []))
    monkeypatch.setattr(coverage,'documents',lambda *a:iter([]))
    result=coverage.financials({'cik':'0000001001','filings':[]},'2026-11-16T00:00:00Z')[0]['ttm_revenue']
    assert result['value']=='371' and result['period_days']==371
    assert result['period_classification']=='LATEST_FISCAL_YEAR' and result['period']['basis']=='latest_fiscal_year'


@pytest.mark.parametrize('change',['issuer','future','hash'])
def test_document_identity_availability_and_hash(stored,change):
    snapshot,at,_=stored;snapshot=deepcopy(snapshot)
    snapshot['filings']=[f for f in snapshot['filings'] if f['form'] in {'10-K','10-Q'}]
    for filing in snapshot['filings']:filing['is_amendment']=False
    f=next(f for f in snapshot['filings'] if f['document_source_ids']);sid=f['document_source_ids'][0];s=snapshot['sources'][sid]
    if change=='issuer':s['source_url']=s['source_url'].replace('/1001/','/9999/')
    if change=='hash':s['raw_sha256']='f'*64
    if change=='future':
        for source in snapshot['sources'].values():source['available_at']='2099-01-01T00:00:00Z'
        assert list(coverage.documents(snapshot,at))==[];return
    with pytest.raises(ValueError):list(coverage.documents(snapshot,at))


def test_bounded_extractive_claim_is_company_statement_not_independent_moat(monkeypatch):
    text=b'<div>Item 1. Business</div><p>We manufacture DRAM and NAND memory for data centers and other customers, and face a cyclical memory market.</p><div>Item 1A. Risk Factors</div><p>DRAM selling prices fluctuate and may adversely affect our customers and manufacturing capacity investments.</p>'
    f={'form':'10-K','accession':'TEST','filed_date':'2026-10-01'};s={'source_url':'https://example.invalid/test','raw_path':'ILLUSTRATIVE','raw_sha256':'hash','available_at':'2026-10-02T00:00:00Z'}
    monkeypatch.setattr(business,'documents',lambda *a:iter([(f,'doc',s,text,[],{})]))
    snapshot={'cik':'0000001001','identity':{'symbols':['TEST']}}
    facts,refs,inventory=business.extract(snapshot,'2026-11-01T00:00:00Z')
    assert facts and any(f['section']=='BUSINESS' for f in facts) and any(f['section']=='RISK_FACTORS' for f in facts)
    assert all(f['kind']=='SOURCE_FACT' and f['evidence_kind']=='OBSERVED' and f['issuer']==snapshot['cik'] for f in facts)
    assert all('market share' not in f['statement'].lower() for f in facts)
    for fact in facts:
        loc=refs[fact['id']]['locator'];span=text.decode()[loc['char_start']:loc['char_end']]
        assert fact['source_text'] in span


def test_thesis_without_financial_support_is_rejected():
    value={'source_references':{},'business_profile':[],'facts':[], 'investment_theses':[{'kind':'ANALYST_INTERPRETATION','financial_context':[],'supporting_facts':[]} ]}
    with pytest.raises(ValueError,match='UNSUPPORTED_THESIS'):business.validate(value)


def test_etf_has_no_corporate_business_or_industry_inventions():
    value={'market_snapshot':{'security_type':'ETF'}}
    business.enrich(value,{'unused':True},'2026-11-01T00:00:00Z')
    assert not value['business_segments'] and not value['investment_theses'] and not value['business_profile'] and not value['recent_events']


def test_existing_evodds_policy_explicitly_withholds_incomplete_components():
    policy=coverage.DEBT_POLICY
    assert policy['operating_leases'].startswith('exclude') and policy['finance_leases'].startswith('include')
    metrics=[{'metric_name':'EV','value':None,'calculation_status':'UNAVAILABLE','input_refs':[],'missing_inputs':['COMPLETE_DEBT']}]
    audit=coverage.audit(metrics,{},[],[])[0]
    assert audit['debt_policy']==policy and audit['reason_categories'] and audit['status']=='UNAVAILABLE'


@pytest.mark.parametrize('change',['normal','unreconciled','prior_quarter','geography','other_issuer','other_currency'])
def test_segment_actual_extraction_math_and_reconciliation(monkeypatch,change):
    a=numeric('us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',120,ordinal=1)
    a['context_ref']='segment'
    prior={**a,'value':'100','start':'2025-01-01','end':'2025-12-31','ordinal':2}
    op={**a,'tag':'us-gaap:OperatingIncomeLoss','value':'30','ordinal':3}
    total={**a,'context_ref':'total','value':'120','ordinal':4}
    if change=='unreconciled':total['value']='150'
    if change=='prior_quarter':prior['start']='2025-10-01'
    if change=='other_issuer':a['same_entity']=False
    if change=='other_currency':a['unit']='EUR'
    dims={'segment':[('explicitmember','us-gaap:StatementBusinessSegmentsAxis','test:SubscriptionsMember')],'total':[]}
    if change=='geography':dims['segment']=[('explicitmember','srt:StatementGeographicalAxis','country:US')]
    f={'form':'10-K','report_date':'2026-12-31','accession':'TEST','filed_date':'2027-01-01'}
    s={'source_url':'https://example.invalid/ILLUSTRATIVE','raw_path':'ILLUSTRATIVE','raw_sha256':'HASH','available_at':'2027-01-02T00:00:00Z'}
    monkeypatch.setattr(segments,'documents',lambda *a:iter([(f,'doc',s,b'', [a_fact,prior,op,total],dims)]))
    a_fact=a
    groups,refs=segments.extract({},'2027-01-03T00:00:00Z')
    if change in {'geography','other_issuer','other_currency'}:assert not groups;return
    g=groups[0];x=g['segments'][0]
    assert Decimal(x['segment_profit_margin'])==25 and x['segment_revenue']=='120'
    assert x['segment_growth_rate'] is None if change=='prior_quarter' else Decimal(x['segment_growth_rate'])==20
    assert g['reconciliation_status']==('RECONCILIATION_REQUIRED' if change=='unreconciled' else 'RECONCILED')
    assert all(r in refs for r in x['source_refs'])


def test_source_fact_and_interpretation_cannot_be_interchanged():
    value={'source_references':{'ref':{}},'business_profile':[{'id':'fact','kind':'ANALYST_INTERPRETATION','source_refs':['ref']}],
        'facts':[],'investment_theses':[],'counter_theses':[]}
    with pytest.raises(ValueError,match='UNSUPPORTED_BUSINESS_FACT'):business.validate(value)


def test_calculated_ratio_carries_fiscal_classification_by_fact_id_without_mutation():
    from extensions.personal_research.tests.test_valuation import inputs,AT
    market,finance=inputs();x=finance['ttm_revenue']
    # Logical finance key and evidence id differ, as they do in real source records.
    x.update(period_classification='LATEST_FISCAL_YEAR',period_days=371)
    x['period']={'start':'2025-08-29','end':'2026-09-03','basis':'latest_fiscal_year'}
    original=deepcopy(finance)
    value=next(x for x in coverage.calculate(market,finance,AT) if x['metric_name']=='P/S')
    assert Decimal(value['value'])==2 and value['period_classification']=='LATEST_FISCAL_YEAR'
    assert value['fiscal_period']['basis']=='latest_fiscal_year' and value['period_days']==371
    assert finance==original


def test_fiscal_display_classifies_fcf_even_when_frozen_builder_shares_ocf_period(monkeypatch):
    period={'start':'2025-08-29','end':'2026-09-03','basis':'TTM'}
    ocf={'id':'ocf','value':'100','unit':'USD','scope':'consolidated','period':period,
         'formula':'latest_disclosed_annual_period_is_complete_trailing_fiscal_year'}
    fcf={**ocf,'id':'fcf','value':'80','formula':'TTM_OCF-TTM_cash_capex'}
    monkeypatch.setattr(coverage.valuation,'financials',lambda *a:({'ttm_operating_cash_flow':ocf,'ttm_fcf':fcf},{},[]))
    monkeypatch.setattr(coverage,'documents',lambda *a:iter([]))
    result=coverage.financials({'cik':'0000001001','filings':[]},'2026-11-16T00:00:00Z')[0]
    assert all(x['period_classification']=='LATEST_FISCAL_YEAR' and x['period_days']==371 for x in result.values())
    assert result['ttm_fcf']['period'] is not result['ttm_operating_cash_flow']['period']
