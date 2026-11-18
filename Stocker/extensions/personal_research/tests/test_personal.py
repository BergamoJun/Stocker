"""Offline ILLUSTRATIVE software checks. No human reviews or market gold."""
from copy import deepcopy
from pathlib import Path
from decimal import Decimal
import json
import pytest
from research import sec_review_demo, sec_refresh_demo, sec_refresh
from research.sec_transport import Client
from extensions.personal_research import core, sources, report
from extensions.personal_research.__main__ import main

AT = '2026-11-12T16:00:00Z'


def fact(name, value, *, start='2026-07-01', end='2026-09-30', unit='USD', basis='quarter', scope='consolidated', role='current'):
    return {'id':role+':'+name, 'name':name,'value':str(value) if value is not None else None,'unit':unit,
        'period':{'start':start,'end':end,'basis':basis},'role':role,'scope':scope,'as_of':AT,
        'accession':'TEST_ACC','available_at':'2026-11-10T00:00:00Z','evidence_kind':'OBSERVED' if value is not None else 'UNKNOWN',
        'source_refs':['fixture:'+role+':'+name], 'assumptions':[],'limitations':[]}


@pytest.fixture
def stored(tmp_path):
    routes,watch=sec_review_demo.fixture();watch['entities']=watch['entities'][:1]
    clock=sec_refresh_demo.Clock();client=Client('Stocker fixture analyst@fixture.invalid',root=tmp_path/'http',
        raw_root=tmp_path/'raw',illustrative=True,opener=sec_refresh_demo.Opener(routes),clock=clock,sleep=clock.advance)
    store=tmp_path/'source';sec_refresh.refresh(watch,client=client,store=store,clock=clock)
    clock.advance(2*86400)
    return sec_refresh.latest(store,'0000001001'),clock(),store


def build(stored,**kw):
    s,at,_=stored
    return core.build('DEMOA',at,snapshot=s,illustrative=True,**kw)


def test_personal_core_needs_no_human_review_or_owner(stored):
    value,_=build(stored)
    assert value['core_financial_status']=='AVAILABLE' and value['analysis_status']=='PARTIAL'
    assert any(f['evidence_kind']=='OBSERVED' for f in value['facts'])
    assert value['exact_interest_impact'] is None and not value['contract_verified']
    assert value['provenance']['review_receipts_created']==0 and value['is_illustrative']
    assert value['price_target'] is None and value['scoring_status']['value'] is None
    assert all(s['status'] in {'UNAVAILABLE','EXPERIMENTAL'} for s in value['optional_engine_statuses'].values())


def test_pure_existing_financial_arithmetic_and_provenance():
    fs=[fact('revenue',100),fact('operating_income',20),fact('operating_cash_flow',30,basis='cumulative_ytd'),fact('capital_expenditure',10,basis='cumulative_ytd')]
    ds,score=core.metrics(fs,AT);d={d['name']:d for d in ds}
    assert Decimal(d['operating_margin_percent']['value'])==20
    assert Decimal(d['free_cash_flow']['value'])==20
    assert d['free_cash_flow']['input_ids']==['current:capital_expenditure','current:operating_cash_flow'] or set(d['free_cash_flow']['input_ids'])=={'current:capital_expenditure','current:operating_cash_flow'}
    assert all(d['source_refs'] and d['formula'] for d in ds)
    assert len(score['available_factors'])+len(score['missing_factors'])==23 and score['value'] is None


@pytest.mark.parametrize('change',['period','unit','scope','basis','zero_denominator','negative_capex','missing'])
def test_incompatible_inputs_never_default_or_combine(change):
    a=fact('revenue',100);b=fact('operating_income',20)
    if change=='period':b['period']['start']='2026-01-01'
    if change=='unit':b['unit']='EUR'
    if change=='scope':b['scope']='parent_attributable'
    if change=='basis':b['period']['basis']='annual'
    if change=='zero_denominator':a['value']='0'
    if change=='missing':a['value']=None;a['evidence_kind']='UNKNOWN'
    if change=='negative_capex':
        a=fact('operating_cash_flow',20,basis='cumulative_ytd');b=fact('capital_expenditure',-1,basis='cumulative_ytd')
    ds,_=core.metrics([a,b],AT)
    assert not ds


def test_zero_is_a_real_observation_not_missing():
    ds,_=core.metrics([fact('revenue',100),fact('operating_income',0)],AT)
    assert Decimal(ds[0]['value'])==0 and ds[0]['evidence_kind']=='DERIVED'


@pytest.mark.parametrize('prior,expected',[('0',None),('-5',None),('50','100.000000')])
def test_growth_preserves_negative_or_zero_prior(prior,expected):
    fs=[fact('revenue',100),fact('revenue',prior,start='2025-07-01',end='2025-09-30',role='prior_year_same_filing')]
    ds,_=core.metrics(fs,AT);g=next(d for d in ds if 'growth' in d['name'])
    assert g['value']==expected


def test_growth_cannot_join_different_filing_or_period():
    p=fact('revenue',50,start='2025-01-01',end='2025-09-30',role='prior_year_same_filing')
    ds,_=core.metrics([fact('revenue',100),p],AT);assert not any('growth' in d['name'] for d in ds)
    p['period']['start']='2025-07-01';p['accession']='OTHER'
    assert not any('growth' in d['name'] for d in core.metrics([fact('revenue',100),p],AT)[0])


def test_unknown_symbol_generates_honest_error_report(tmp_path):
    value,_=core.build('NOTSTORED',AT,source_store=tmp_path)
    assert value['analysis_status']=='UNAVAILABLE' and value['issuer'] is None
    assert not value['facts'] and not value['estimates']
    assert '수치와 투자 논거를 생성하지 않았습니다' in report.render(value)


@pytest.mark.parametrize('change',['future_snapshot','future_source','raw_tamper','wrong_ticker','real_namespace','fund'])
def test_source_identity_hash_and_point_in_time_fail_closed(stored,change):
    s,at,_=stored;s=deepcopy(s)
    if change=='future_snapshot':s['available_at']='2099-01-01T00:00:00Z'
    if change=='future_source':next(iter(s['sources'].values()))['available_at']='2099-01-01T00:00:00Z'
    if change=='raw_tamper':Path(next(iter(s['sources'].values()))['raw_path']).write_bytes(b'corrupt')
    if change=='wrong_ticker':s['identity']['symbols']=['OTHER']
    if change=='fund':s['entity_type']='investment'
    value,_=core.build('DEMOA',at,snapshot=s,illustrative=change!='real_namespace')
    assert value['analysis_status']=='UNAVAILABLE' and not value['facts'] and value['error']


def test_companyfacts_fallback_has_exact_json_field_path(stored):
    s,at,_=stored;s=deepcopy(s)
    for f in s['filings']:f['document_source_ids']=[]
    value,_=core.build('DEMOA',at,snapshot=s,illustrative=True)
    facts=[f for f in value['facts'] if f['value'] is not None]
    assert facts and all(f['source_verification']=='STANDARD_API_FIELD_MATCH' for f in facts)
    assert all(value['source_references'][f['source_refs'][0]]['locator']['json_path'].startswith('facts.us-gaap.') for f in facts)


def test_amendment_does_not_clear_data_by_api_fallback(stored):
    s,at,_=stored;s=deepcopy(s)
    for f in s['filings']:
        if f['form'] in {'10-Q','10-K'}:f['is_amendment']=True;f['document_source_ids']=[]
    value,_=core.build('DEMOA',at,snapshot=s,illustrative=True)
    assert all(f['value'] is None for f in value['facts'])


def test_optional_engine_failure_leaves_fundamentals(stored,monkeypatch):
    def fail():raise ValueError('OPTIONAL_TEST_FAILURE')
    monkeypatch.setattr(core.weighted_engine,'policy',fail)
    value,_=build(stored)
    assert value['core_financial_status']=='AVAILABLE' and value['derived_metrics']==[]
    assert value['scoring_status']['status']=='UNAVAILABLE'


def test_estimates_require_assumptions_and_unknown_cannot_be_zero(stored):
    v,_=build(stored);v=deepcopy(v);v['estimates'][0]['assumptions']=[]
    with pytest.raises(ValueError,match='ASSUMPTIONS'):core.validate(v)
    v,_=build(stored);v['facts'][0].update(evidence_kind='UNKNOWN',value='0')
    with pytest.raises(ValueError,match='UNKNOWN'):core.validate(v)


def test_report_immutable_replay_and_explicit_illustrative_label(stored,tmp_path,monkeypatch):
    monkeypatch.setattr(report,'ROOT',tmp_path)
    v,s=build(stored);folder=report.write(v,s)
    assert report.validate(folder)['status']=='VALID'
    assert 'ILLUSTRATIVE' in (folder/'REPORT.ko.md').read_text(encoding='utf-8')
    (folder/'REPORT.ko.md').write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='BYTES_CHANGED'):report.validate(folder)


def test_cli_has_no_owner_or_reviewer_arguments(capsys):
    with pytest.raises(SystemExit) as e:main(['--help'])
    assert e.value.code==0
    help=capsys.readouterr().out
    assert '--ticker' in help and '--validate' in help and '--reviewer' not in help and '--owner' not in help


def test_date_cutoff_is_korean_end_of_day():
    assert core.cutoff('2026-11-12')=='2026-11-12T14:59:59.999999Z'
