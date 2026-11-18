"""ILLUSTRATIVE provider responses; no vendor prices or credentials in fixtures."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from research import fetch_prices
from research.panel_transport import detail_url,price_url,SourceUnavailable
from extensions.personal_research import market_data as market
from extensions.personal_research.__main__ import main
from extensions.personal_research.tests.test_personal import stored

AT='2026-11-15T12:00:00Z'


def packet(tmp_path,*,ticker='TEST',kind='CS',dates=('2026-11-12','2026-11-13'),retrieved='2026-11-14T02:00:00Z'):
    folder=tmp_path/'sources';folder.mkdir();evidence={}
    def put(name,payload,url):
        raw=json.dumps(payload).encode();(folder/name).write_bytes(raw)
        evidence[name]={'source_url':url,'retrieved_at':retrieved,'raw_sha256':hashlib.sha256(raw).hexdigest()}
    put('identity.json',{'status':'OK','results':{'ticker':ticker,'active':True,'market':'stocks','locale':'us','currency_name':'usd',
        'type':kind,'primary_exchange':'XNAS' if kind=='CS' else 'ARCX','share_class_figi':'TEST_FIGI','cik':'1001',
        'market_cap':1000,'weighted_shares_outstanding':10,'share_class_shares_outstanding':10}},detail_url(ticker,dates[-1]))
    for adjusted,name,close in [(False,'raw.json',100),(True,'adjusted.json',50)]:
        rows=[{'t':int(datetime.fromisoformat(d+'T00:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1000),
            'o':close,'h':close+1,'l':close-1,'c':close,'v':20} for d in dates]
        put(name,{'status':'OK','ticker':ticker,'adjusted':adjusted,'results':rows},price_url(ticker,dates[0],dates[-1]).replace('adjusted=true','adjusted='+str(adjusted).lower()))
    record=fetch_prices.source_record();record.update(is_illustrative=True,account_eligibility_verified=True,account_confirmed_at='2026-11-10T00:00:00Z')
    return {'ticker':ticker,'available_at':retrieved,'is_illustrative':True,'entitlement':record,'folder':str(folder),'evidence':evidence}


def test_real_close_adjusted_close_and_provider_identity_separate(tmp_path):
    p=packet(tmp_path);m=market.normalize(p,AT,expected_cik='0000001001',illustrative=True)
    assert m['raw_close']=='100' and m['adjusted_close']=='50' and m['data_status']=='AVAILABLE'
    assert m['trading_date']=='2026-11-13' and m['security_id']=='TEST_FIGI'
    assert not m['point_in_time_verified'] and m['market_cap_scope']=='PROVIDER_ALL_COMMON_EQUITY_AS_CONVERTED'


@pytest.mark.parametrize('at',['2026-11-14T13:00:00Z',AT,'2026-11-16T13:00:00Z','2026-11-16T18:00:00Z'])
def test_weekend_premarket_intraday_and_timezone_boundary_use_completed_friday(tmp_path,at):
    p=packet(tmp_path);m=market.normalize(p,at,illustrative=True)
    assert m['trading_date']=='2026-11-13' and m['data_status']=='AVAILABLE'


def test_us_holiday_selects_prior_session(tmp_path):
    p=packet(tmp_path,dates=('2026-11-25',),retrieved='2026-11-26T02:00:00Z')
    m=market.normalize(p,'2026-11-26T18:00:00Z',illustrative=True)
    assert m['trading_date']=='2026-11-25' and m['data_status']=='AVAILABLE'


def test_stale_and_future_vintage_are_not_current_or_historical_proof(tmp_path):
    p=packet(tmp_path)
    assert market.normalize(p,'2026-11-16T23:00:00Z',illustrative=True)['data_status']=='STALE_DATA'
    with pytest.raises(ValueError,match='NOT_AVAILABLE'):market.normalize(p,'2026-11-13T13:00:00Z',illustrative=True)


def test_incomplete_today_cannot_supply_daily_close(tmp_path):
    p=packet(tmp_path,retrieved='2026-11-13T18:00:00Z')
    with pytest.raises(ValueError,match='INCOMPLETE'):market.normalize(p,'2026-11-13T19:00:00Z',illustrative=True)


@pytest.mark.parametrize('change',['symbol','currency','share_class','inactive','ADR','cik','hash','dividend_basis','partial'])
def test_identity_basis_and_integrity_fail_closed(tmp_path,change):
    p=packet(tmp_path);name='identity.json';path=Path(p['folder'])/name;v=json.loads(path.read_bytes())
    if change=='symbol':v['results']['ticker']='OTHER'
    if change=='currency':v['results']['currency_name']='eur'
    if change=='share_class':v['results']['share_class_figi']=None
    if change=='inactive':v['results']['active']=False
    if change=='ADR':v['results']['type']='ADRC'
    if change=='cik':v['results']['cik']='999'
    if change=='hash':v['tampered']=True
    if change in {'dividend_basis','partial'}:
        name='raw.json';path=Path(p['folder'])/name;v=json.loads(path.read_bytes())
        if change=='dividend_basis':v['adjusted']=True
        else:v['next_url']='https://untrusted.invalid/page'
    raw=json.dumps(v).encode();path.write_bytes(raw)
    if change!='hash':p['evidence'][name]['raw_sha256']=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):market.normalize(p,AT,expected_cik='0000001001',illustrative=True)


def test_etf_has_market_identity_but_no_company_capitalization(tmp_path):
    m=market.normalize(packet(tmp_path,ticker='SPY',kind='ETF'),AT,illustrative=True)
    assert m['security_type']=='ETF' and m['provider_reported_market_cap'] is None


@pytest.mark.parametrize('code',['LOCAL_KEY_MISSING','HTTP_404','HTTP_429','TRANSPORT_FAILURE'])
def test_fetch_failure_is_separate_and_counts_attempts(monkeypatch,code):
    monkeypatch.setattr(market,'lookup',lambda *a,**k:market.unavailable('TEST'))
    def fail(*a,**kw):
        e=SourceUnavailable(code);e.personal_requests=1;raise e
    monkeypatch.setattr(market,'capture',fail)
    m=market.get('TEST',AT,fetch_missing=True)
    assert m['raw_close'] is None and m['data_status']==code and m['network_requests']==1


def test_offline_never_opens_transport_and_saved_replay_checks_hash(tmp_path,monkeypatch):
    p=packet(tmp_path);m=market.normalize(p,AT,illustrative=True)
    def forbidden(*a,**kw):raise AssertionError('unexpected HTTP')
    monkeypatch.setattr(market,'capture',forbidden)
    assert market.get('TEST',AT,saved=m,illustrative=True)['raw_close']=='100'
    (Path(p['folder'])/'raw.json').write_bytes(b'tampered')
    assert market.get('TEST',AT,saved=m,illustrative=True)['raw_close'] is None


def test_offline_and_fetch_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:main(['--ticker','MSFT','--offline','--fetch-missing'])
    assert error.value.code==2


def test_failed_refresh_keeps_valid_stale_observation_separate(tmp_path,monkeypatch):
    p=packet(tmp_path);m=market.normalize(p,'2026-11-16T23:00:00Z',illustrative=True)
    monkeypatch.setattr(market,'lookup',lambda *a,**kw:m)
    def fail(*a,**kw):raise SourceUnavailable('HTTP_403')
    monkeypatch.setattr(market,'capture',fail)
    result=market.get('TEST','2026-11-16T23:00:00Z',fetch_missing=True)
    assert result['data_status']=='STALE_DATA' and result['raw_close']=='100' and result['acquisition_error']=='HTTP_403'


def test_newly_fetched_SEC_identity_is_rechecked_for_explicit_cutoff(stored,tmp_path,monkeypatch):
    from extensions.personal_research import core
    _,at,source=stored;target=tmp_path/'new_sec'
    monkeypatch.setattr(market,'SEC_STORE',target)
    def acquire(ticker):
        shutil.copytree(source,target)
        return {'status':'completed','network_requests':0}
    monkeypatch.setattr(market,'fetch_sec',acquire)
    monkeypatch.setattr(market,'get',lambda *a,**kw:{'resolved_ticker':'DEMOA','security_type':'EQUITY','cik':'0000000999',
        'raw_close':'100','data_status':'AVAILABLE','currency':'USD','network_requests':0})
    original=core._fundamentals
    def fixture_fundamentals(*args,**kwargs):
        kwargs['illustrative']=True
        return original(*args,**kwargs)
    monkeypatch.setattr(core,'_fundamentals',fixture_fundamentals)
    value,_=core.build('DEMOA',at,source_store=tmp_path/'empty',fetch_missing=True)
    assert value['is_illustrative'] and value['core_financial_status']=='AVAILABLE'
    assert value['market_data_status']=='SYMBOL_CIK_MISMATCH' and value['market_snapshot']['raw_close'] is None
