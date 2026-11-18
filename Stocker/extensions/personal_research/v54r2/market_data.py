"""Small Massive EOD adapter over the existing rights/cache/shared request queue."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo
import uuid
from research import fetch_prices, sec_refresh
from research.local_files import atomic_json
from research.panel_transport import detail_url, price_url, validate_url, Deferred, SourceUnavailable
from research.rights import as_utc, require_entitlement, price_uses
from research.sec_report import ROOT
from research.sec_transport import now
from research.swing_models import XnysCalendar
from research.validation import digest
from extensions import market_inputs, market_client, scope

STORE = ROOT/'reports/private/personal_market/real'
SEC_STORE = ROOT/'reports/private/personal_sec/real'


def number(value, *, positive=False):
    if isinstance(value, bool) or value is None: raise ValueError('INVALID_MARKET_NUMBER')
    d=Decimal(str(value))
    if not d.is_finite() or (positive and d<=0): raise ValueError('INVALID_MARKET_NUMBER')
    return d


def identity(raw, ticker):
    payload=json.loads(raw,parse_float=Decimal);r=payload.get('results',{})
    if payload.get('status')!='OK' or r.get('ticker')!=ticker or r.get('active') is not True or r.get('market')!='stocks' or r.get('locale')!='us':
        raise ValueError('SYMBOL_AMBIGUOUS')
    if r.get('type') not in {'CS','ETF'} or r.get('primary_exchange') not in {'XNYS','XNAS','XASE','ARCX'} or r.get('currency_name','').lower()!='usd':
        raise ValueError('UNSUPPORTED_SECURITY_OR_CURRENCY')
    security=r.get('share_class_figi') or (r.get('composite_figi') if r['type']=='ETF' else None)
    if not security: raise ValueError('SHARE_CLASS_ID_REQUIRED')
    return {'requested_ticker':ticker,'resolved_ticker':r['ticker'],'security_id':security,
        'security_type':'ETF' if r['type']=='ETF' else 'EQUITY','exchange':r['primary_exchange'],'currency':'USD',
        'cik':str(r['cik']).zfill(10) if r.get('cik') else None,'name':r.get('name'),
        'composite_figi':r.get('composite_figi'),'share_class_figi':r.get('share_class_figi')},r


def bars(raw, evidence, ticker):
    """Existing adjusted-only normalizers stay frozen. Validate raw OHLC separately."""
    validate_url(evidence['source_url']);parts=urlparse(evidence['source_url']).path.split('/')
    if len(parts)!=10 or parts[4]!=ticker or parts[5:8]!=['range','1','day']: raise ValueError('PRICE_IDENTITY_MISMATCH')
    adjusted=parse_qs(urlparse(evidence['source_url']).query).get('adjusted')
    if adjusted not in [['true'],['false']]: raise ValueError('ADJUSTMENT_BASIS_REQUIRED')
    if hashlib.sha256(raw).hexdigest()!=evidence['raw_sha256']: raise ValueError('MARKET_SOURCE_HASH_CHANGED')
    p=json.loads(raw,parse_float=Decimal,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_PRICE')))
    if p.get('ticker')!=ticker or p.get('status') not in {'OK','DELAYED'} or p.get('adjusted') is not (adjusted==['true']) or p.get('next_url'):
        raise ValueError('PRICE_IDENTITY_OR_PARTIAL_RESPONSE')
    rows=p.get('results',[])
    if not isinstance(rows,list) or not 1<=len(rows)<=550: raise ValueError('PRICE_ROWS_MISSING_OR_EXCESSIVE')
    cal=XnysCalendar((date.fromisoformat(parts[8])-timedelta(days=10)).isoformat(),(date.fromisoformat(parts[9])+timedelta(days=10)).isoformat())
    result=[]
    for index,row in enumerate(rows):
        if type(row['t']) is not int: raise ValueError('INVALID_BAR_TIMESTAMP')
        local=datetime.fromtimestamp(row['t']/1000,timezone.utc).astimezone(ZoneInfo('America/New_York'));session=local.date().isoformat()
        if local.time().isoformat()!='00:00:00' or not parts[8]<=session<=parts[9]: raise ValueError('INVALID_BAR_SESSION')
        o,h,l,c=[number(row[k],positive=True) for k in ['o','h','l','c']];v=number(row['v'])
        if v<0 or not l<=min(o,c)<=max(o,c)<=h: raise ValueError('INVALID_OHLCV')
        ended=cal.close(session)
        if as_utc(ended)>as_utc(evidence['retrieved_at']): raise ValueError('INCOMPLETE_MARKET_SESSION')
        result.append({'trading_date':session,'close':str(c),'volume':str(v),'market_session_end':ended,'provider_row_index':index})
    if [r['trading_date'] for r in result]!=sorted({r['trading_date'] for r in result}): raise ValueError('DUPLICATE_OR_UNSORTED_BARS')
    return result,adjusted==['true']


def unavailable(ticker, code='MARKET_DATA_UNAVAILABLE'):
    return {'requested_ticker':ticker,'resolved_ticker':None,'data_status':code,'raw_close':None,'adjusted_close':None,
        'source_provider':'massive','source_reference':None,'trading_date':None,'limitations':[], 'network_requests':0}


def normalize(packet, cutoff, *, expected_cik=None, illustrative=False):
    if packet['is_illustrative'] is not illustrative: raise ValueError('MARKET_NAMESPACE_MISMATCH')
    require_entitlement(packet['entitlement'],price_uses('local_research'),cutoff,synthetic=illustrative)
    if as_utc(packet['available_at'])>as_utc(cutoff): raise ValueError('MARKET_NOT_AVAILABLE_AT_CUTOFF')
    folder=Path(packet['folder']);raws={}
    if (folder/'record.json').exists() and sec_refresh.read(folder/'record.json')!=packet:
        raise ValueError('MARKET_REGISTRATION_CHANGED')
    if packet.get('legacy_record_path'):
        if digest(packet['legacy_record_path'])!=packet['legacy_record_sha256']: raise ValueError('LEGACY_REGISTRATION_CHANGED')
        market_inputs.record(packet['legacy_record_path'])
    for name,evidence in packet['evidence'].items():
        actual=packet.get('file_names',{}).get(name,name)
        if Path(name).name!=name or Path(actual).name!=actual: raise ValueError('MARKET_PATH_ESCAPE')
        data=(folder/actual).read_bytes()
        if hashlib.sha256(data).hexdigest()!=evidence['raw_sha256']: raise ValueError('MARKET_SOURCE_HASH_CHANGED')
        if as_utc(evidence['retrieved_at'])>as_utc(packet['available_at']): raise ValueError('MARKET_CAPTURE_BACKDATED')
        raws[name]=data
    who,ref=identity(raws['identity.json'],packet['ticker'])
    refdate=parse_qs(urlparse(packet['evidence']['identity.json']['source_url']).query).get('date',[None])[0]
    if packet['evidence']['identity.json']['source_url']!=detail_url(packet['ticker'],refdate): raise ValueError('REFERENCE_IDENTITY_MISMATCH')
    if expected_cik and who['cik']!=expected_cik: raise ValueError('SYMBOL_CIK_MISMATCH')
    series=[]
    for name in ['raw.json','adjusted.json']:
        if name in raws:
            rows,adjusted=bars(raws[name],packet['evidence'][name],packet['ticker']);series.append((rows,adjusted,name))
    eligible=[r for rows,_,_ in series for r in rows if as_utc(r['market_session_end'])<=as_utc(cutoff)]
    if not eligible: raise ValueError('NO_COMPLETED_MARKET_SESSION')
    session=max(r['trading_date'] for r in eligible)
    if refdate!=session: raise ValueError('REFERENCE_PRICE_DATE_MISMATCH')
    chosen={a:(next((r for r in rows if r['trading_date']==session),None),name) for rows,a,name in series}
    rawrow,rawname=chosen.get(False,(None,None));adjrow,adjname=chosen.get(True,(None,None));row=rawrow or adjrow
    expected=market_inputs.latest_completed(cutoff);current=session==expected
    source_name=rawname or adjname;e=packet['evidence'][source_name]
    refs=[{'url':x['source_url'],'raw_sha256':x['raw_sha256'],'retrieved_at':x['retrieved_at'],'raw_path':str(folder/packet.get('file_names',{}).get(n,n))} for n,x in packet['evidence'].items()]
    cap=ref.get('market_cap');cap=str(number(cap,positive=True)) if cap is not None and who['security_type']=='EQUITY' else None
    return {**who,'exchange_timezone':'America/New_York','requested_as_of':cutoff,'trading_date':session,
        'raw_close':rawrow['close'] if rawrow else None,'adjusted_close':adjrow['close'] if adjrow else None,
        'adjustment_basis':'RAW_AS_TRADED_AND_SPLIT_ADJUSTED_NOT_DIVIDEND' if rawrow and adjrow else 'RAW_AS_TRADED' if rawrow else 'SPLIT_ADJUSTED_CURRENT_SHARE_BASIS_NOT_DIVIDEND',
        'volume':row['volume'],'market_session_end':row['market_session_end'],'retrieved_at':e['retrieved_at'],
        'price_locator':{'json_path':f'results[{row["provider_row_index"]}].c','source_url':e['source_url'],'raw_sha256':e['raw_sha256']},
        'source_available_at':packet['available_at'],'source_provider':'massive','source_reference':refs,
        'data_status':'AVAILABLE' if current else 'STALE_DATA','price_kind':'COMPLETED_EOD_NOT_INTRADAY',
        'provider_reported_market_cap':cap,'market_cap_reference_date':refdate,
        'market_cap_scope':'PROVIDER_ALL_COMMON_EQUITY_AS_CONVERTED' if cap and ref.get('weighted_shares_outstanding') is not None and number(ref['weighted_shares_outstanding'],positive=True) else None,
        'provider_reported_share_class_shares':str(ref.get('share_class_shares_outstanding')) if ref.get('share_class_shares_outstanding') is not None else None,
        'provider_reported_all_class_converted_shares':str(ref.get('weighted_shares_outstanding')) if ref.get('weighted_shares_outstanding') is not None else None,
        'point_in_time_verified':False,'source_packet':packet,'network_requests':0,
        'limitations':['일별 종가이며 실시간 시세가 아닙니다.','현재 취득한 과거 bar는 원래 시점의 관측 vintage를 증명하지 않습니다.',
                       '제공처 시가총액은 요청한 reference-date의 보고값이며 독립 재계산/기업행동 검증과 구분합니다.']}


def packets(ticker, cutoff, store=STORE):
    for p in sorted(Path(store).glob('*/record.json')):
        v=sec_refresh.read(p)
        if v['ticker']==ticker and as_utc(v['available_at'])<=as_utc(cutoff): yield v
    # Existing captures have raw provider responses, not unlabelled price numbers.
    for p in sorted((ROOT/'reports/private/market_inputs/real').glob('*/record.json')):
        folder,v=market_inputs.record(p)
        if as_utc(v['available_at'])>as_utc(cutoff) or v['instruments'].get(ticker,{}).get('status')!='captured': continue
        item=v['instruments'][ticker]
        evidence={'identity.json':item['identity_evidence'],'adjusted.json':item['daily_evidence']}
        # A read-only alias maps names onto the old sealed source paths.
        yield {'ticker':ticker,'available_at':v['available_at'],'is_illustrative':False,'entitlement':v['entitlement'],
            'folder':str(folder/'sources'),'evidence':evidence,'file_names':{'identity.json':ticker+'.identity.json','adjusted.json':ticker+'.daily.json'},
            'legacy_record_path':str(p.resolve()),'legacy_record_sha256':digest(p)}


def lookup(ticker, cutoff, *, expected_cik=None, store=STORE, illustrative=False):
    if illustrative:return unavailable(ticker)
    found=list(packets(ticker,cutoff,store))
    if not found:return unavailable(ticker)
    chosen=max(found,key=lambda v:as_utc(v['available_at']))
    return normalize(chosen,cutoff,expected_cik=expected_cik)


def baseline():
    candidates=[]
    for p in list((ROOT/'reports/private/personal_market_p2').glob('*/PREFLIGHT.json'))+list((ROOT/'reports/private/personal_intelligence').glob('*/PREFLIGHT.json'))+list((ROOT/'reports/private/personal_p4').glob('*/PREFLIGHT.json'))+list((ROOT/'reports/private/personal_p5').glob('*/PREFLIGHT.json'))+list((ROOT/'reports/private/personal_p51').glob('*/PREFLIGHT.json')):
        candidates.append((sec_refresh.read(p)['at'],p.parent))
    for _,folder in sorted(candidates,reverse=True):
        for name in ['final.baseline.json','development.baseline.json']:
            path=folder/name
            if path.exists():
                try:scope.check(path);return path
                except ValueError:continue
    raise ValueError('CURRENT_MARKET_BASELINE_UNAVAILABLE')


def capture(ticker, *, client=None, store=STORE, clock=now):
    if client is None:
        from research.learned_research import latest_training_snapshot
        _,path=latest_training_snapshot()
        client=market_client.Client(sec_refresh.read(path),os.environ.get('MASSIVE_API_KEY',''),baseline(),maximum_requests=3)
    _,end,_=fetch_prices.basic_window(clock(),100);start=(date.fromisoformat(end)-timedelta(days=99)).isoformat()
    before=client.network_requests
    ev={};raws={}
    try:
        for name,url in [('identity.json',detail_url(ticker,end)),('raw.json',price_url(ticker,start,end).replace('adjusted=true','adjusted=false')),
                         ('adjusted.json',price_url(ticker,start,end))]:
            value=client.get(url);ev[name]={k:value[k] for k in ['source_url','retrieved_at','raw_sha256']};raws[name]=value['raw']
    except (ValueError,OSError,Deferred,SourceUnavailable) as error:
        error.personal_requests=client.network_requests-before;raise
    who,_=identity(raws['identity.json'],ticker)
    folder=Path(store)/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=False)
    for name,raw in raws.items():
        with (folder/name).open('xb') as f:f.write(raw)
    packet={'ticker':ticker,'available_at':clock(),'is_illustrative':client.record['is_illustrative'],'entitlement':client.record,
            'folder':str(folder.resolve()),'evidence':ev}
    normalize(packet,clock(),illustrative=client.record['is_illustrative'])
    atomic_json(folder/'record.json',packet)
    return packet,client.network_requests-before


def get(ticker, cutoff, *, expected_cik=None, fetch_missing=False, illustrative=False, saved=None, current_request=False):
    requests=0
    try:
        if saved is not None:
            if saved.get('source_packet'):
                result=normalize(saved['source_packet'],cutoff,expected_cik=expected_cik,illustrative=illustrative)
                if saved.get('acquisition_error'):result['acquisition_error']=saved['acquisition_error']
                result['network_requests']=saved.get('network_requests',0);return result
            if saved.get('raw_close') is not None or saved.get('adjusted_close') is not None:raise ValueError('UNBOUND_MARKET_VALUE')
            return saved
        result=lookup(ticker,cutoff,expected_cik=expected_cik,illustrative=illustrative)
        if fetch_missing and result['data_status']!='AVAILABLE' and not illustrative:
            packet,requests=capture(ticker)
            # Newly downloaded observations are not backdated into a requested historical report.
            result=normalize(packet,now() if current_request else cutoff,expected_cik=expected_cik)
        result['network_requests']=requests;return result
    except (ValueError,OSError,KeyError,ArithmeticError,Deferred,SourceUnavailable) as error:
        code=error.code if isinstance(error,SourceUnavailable) else 'PROVIDER_RATE_LIMIT' if isinstance(error,Deferred) else str(error).split(':')[0]
        if not code.replace('_','').isalnum() or len(code)>100:code='MARKET_DATA_UNAVAILABLE'
        if 'result' in locals() and result.get('source_packet') and result.get('data_status')=='STALE_DATA':
            return {**result,'acquisition_error':code,'network_requests':getattr(error,'personal_requests',requests)}
        return {**unavailable(ticker,code),'network_requests':getattr(error,'personal_requests',requests)}


def fetch_sec(ticker):
    from research.sec_transport import Client,user_agent,Deferred as SecDeferred
    client=Client(user_agent());old=client.on_attempt
    def budget():
        if client.network_requests>=8:raise SecDeferred(now(),'PERSONAL_SEC_REQUEST_CAP')
        old()
    client.on_attempt=budget
    result=sec_refresh.refresh({'entities':[{'symbol':ticker}]},client=client,store=SEC_STORE,max_entities=1)
    return {'status':result['status'],'reason':result.get('reason'),'network_requests':result.get('network_requests',0),'report_path':result.get('report_path')}
