"""Ticker-driven bounded metadata search; no publisher bodies or AI input."""
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import time
import uuid
import jsonschema
from research import issuer_profiles,news,news_refresh,news_archive
from research.local_files import LockBusy
from research.news_connection_report import text,link
from research.panel_transport import Deferred,SourceUnavailable
from research.news_transport import NewsRequestDeferred
from research.project_compatibility import check as historical_check
from research.rights import as_utc,RightsError
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import stable_hash,digest
from . import scope,ondemand_cache as cache,ondemand_transport

FILES=['extensions/'+n for n in ('ondemand_policy.json','ondemand_transport.py','ondemand_cache.py','ondemand_news.py','ondemand_contract.schema.json')]
TOPICS=('company','sector','macro','commodities','politics')
SUCCESS={'links_available','bounded_no_links'}
ROW_KEYS={'source_id','url','title','publisher','source_time','time_kind','publication_verified','impact_assessed'}
VALUE_KEYS={'provider','topic','query','window_start','window_end','checked_at','status','reason_code','retry_at','articles','limit_reached','source_fingerprint'}
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def methods(): return {p:digest(ROOT/p) for p in FILES}

def resolve(ticker,at,*,illustrative=False):
    registry=read(ROOT/'config/issuer_profiles.json'); issuer_profiles.validate(registry,'registry')
    rows=[p for p in registry['profiles'] if p['symbol']==ticker]
    if len(rows)!=1: raise ValueError('UNSUPPORTED_ON_DEMAND_TICKER')
    profile=issuer_profiles.as_of(rows[0]['cik'],at,illustrative=illustrative)
    if profile is None or profile['symbol']!=ticker: raise ValueError('NO_CURRENT_SEC_BOUND_NEWS_PROFILE')
    return profile

def plan(profile,days,at,settings,method_hashes):
    if type(days) is not int or not settings['minimum_lookback_days']<=days<=settings['maximum_lookback_days']: raise ValueError('LOOKBACK_DAYS_OUTSIDE_CAP')
    end=as_utc(at).replace(microsecond=0).isoformat(); start=(as_utc(end)-timedelta(days=days)).isoformat()
    result=[]
    for topic in TOPICS:
        provider='massive' if topic=='company' else 'gdelt_project'
        query=profile['symbol'] if topic=='company' else profile['sector_query'] if topic=='sector' else settings['queries'][topic]
        key={'provider':provider,'topic':topic,'query':query,'days':days,'method_hashes':method_hashes,'issuer_binding':profile['binding'] if topic=='company' else None}
        result.append({'key':key,'provider':provider,'topic':topic,'query':query,'window_start':start,'window_end':end})
    return result

def base_value(item):
    return {k:item[k] for k in ('provider','topic','query','window_start','window_end')} | {'checked_at':None,'status':'unavailable','reason_code':None,'retry_at':None,'articles':[],'limit_reached':False,'source_fingerprint':None}

def article(row,provider,topic):
    url=news.canonical_url(row['url']); title=str(row['title']).replace('\r',' ').replace('\n',' ').strip()[:300]; publisher=str(row['publisher'])[:120]
    title.encode('utf-8'); publisher.encode('utf-8')
    if not title or len(url.encode('utf-8'))>2048: raise ValueError('ARTICLE_METADATA_OUTSIDE_CAP')
    source_time=row['provider_published_at'] if provider=='massive' else row['first_seen_at']
    value={'url':url,'title':title,'publisher':publisher,'source_time':source_time,'time_kind':'provider_publication_assertion' if provider=='massive' else 'index_observation',
        'publication_verified':False,'impact_assessed':False}
    value['source_id']=stable_hash({'provider':provider,**value}); return value

def fetch(item,profile,client,clock):
    value=base_value(item)
    try:
        if item['provider']=='massive':
            response=client.massive(profile['symbol'],item['window_start'],item['window_end'])
            result=news_refresh.normalize_massive(response['raw'],profile,item['window_start'],item['window_end'],response['retrieved_at'])
            rows=result['articles']; dropped=result['dropped_records']; limited=result['pagination_not_followed'] or len(rows)>=25
            fingerprint=response['raw_sha256']; checked=response['retrieved_at']
        else:
            payload=client.gdelt_window(item['query'],item['window_start'],item['window_end']); checked=clock()
            rows,warnings=news.normalize_articles(payload,item['topic'],checked); dropped=len(warnings); limited=len(payload['articles'])>=25
            fingerprint=stable_hash(payload)
        selected=[]
        for row in rows:
            try:
                clean=article(row,item['provider'],item['topic'])
                if not as_utc(item['window_start'])<=as_utc(clean['source_time'])<=as_utc(item['window_end']): raise ValueError('ARTICLE_OUTSIDE_QUERY_WINDOW')
                selected.append(clean)
            except (ValueError,KeyError,TypeError): dropped+=1
        value.update(checked_at=checked,status='partial' if dropped or limited else 'links_available' if selected else 'bounded_no_links',articles=selected,limit_reached=bool(limited),source_fingerprint=fingerprint)
    except (ValueError,OSError,Deferred,SourceUnavailable,NewsRequestDeferred) as error:
        failed=news_refresh.query_failure(item['provider'],error,clock)
        value.update({k:failed.get(k) for k in ('checked_at','status','reason_code','retry_at')})
    return value

def validate_value(v,item,at):
    if set(v)!=VALUE_KEYS or any(v[k]!=item[k] for k in ('provider','topic','query')): raise ValueError('ON_DEMAND_QUERY_BINDING_CHANGED')
    if not as_utc(v['window_start'])<as_utc(v['window_end'])<=as_utc(at): raise ValueError('ON_DEMAND_QUERY_WINDOW_CHANGED')
    if as_utc(v['window_end'])>as_utc(item['window_end']) or as_utc(v['window_end'])-as_utc(v['window_start'])!=timedelta(days=item['key']['days']): raise ValueError('ON_DEMAND_LOOKBACK_BINDING_CHANGED')
    if len(v['articles'])>25 or v['checked_at'] is not None and as_utc(v['checked_at'])<as_utc(v['window_end']): raise ValueError('ON_DEMAND_OBSERVATION_TIME_CHANGED')
    if v['checked_at'] is not None and as_utc(v['checked_at'])>as_utc(at): raise ValueError('ON_DEMAND_FUTURE_OBSERVATION')
    for row in v['articles']:
        if set(row)!=ROW_KEYS or row['publication_verified'] is not False or row['impact_assessed'] is not False: raise ValueError('UNVERIFIED_NEWS_PROMOTED')
        if news.canonical_url(row['url'])!=row['url'] or len(row['url'].encode('utf-8'))>2048 or not 1<=len(row['title'])<=300 or len(row['publisher'])>120: raise ValueError('ON_DEMAND_ARTICLE_CHANGED')
        if not as_utc(v['window_start'])<=as_utc(row['source_time'])<=as_utc(v['window_end']): raise ValueError('ON_DEMAND_ARTICLE_TIME_CHANGED')
        kind='provider_publication_assertion' if v['provider']=='massive' else 'index_observation'
        if row['time_kind']!=kind or row['source_id']!=stable_hash({'provider':v['provider'],**{k:x for k,x in row.items() if k!='source_id'}}): raise ValueError('ON_DEMAND_SOURCE_ID_CHANGED')
    cache.metadata_only(v)
    if v['status'] in SUCCESS|{'partial'}:
        if v['checked_at'] is None or not isinstance(v['source_fingerprint'],str) or len(v['source_fingerprint'])!=64 or v['reason_code'] is not None: raise ValueError('ON_DEMAND_SUCCESS_PROVENANCE_MISSING')
        if v['status']=='bounded_no_links' and v['articles'] or v['status']=='links_available' and not v['articles']: raise ValueError('ON_DEMAND_LINK_STATUS_CHANGED')
    elif v['articles'] or not v['reason_code']: raise ValueError('ON_DEMAND_FAILURE_IS_NOT_EMPTY_SUCCESS')

def assemble(results,start,end):
    groups={}; topics=[]
    for receipt in results:
        v=receipt['value']; lag=max(0,(as_utc(end)-as_utc(v['window_end'])).total_seconds())
        topics.append({'topic':v['topic'],'provider':v['provider'],'status':v['status'],'checked_at':v['checked_at'],'cache_reused':receipt['cache_reused'],'unsearched_tail_seconds':lag if v['checked_at'] is not None else None,'limit_reached':v['limit_reached'],'reason_code':v['reason_code']})
        for row in v['articles']:
            if not as_utc(start)<=as_utc(row['source_time'])<=as_utc(end): continue
            group=groups.setdefault(row['url'],{'id':stable_hash(row['url']),'url':row['url'],'title':row['title'],'topics':[],'sources':[]})
            if v['topic'] not in group['topics']: group['topics'].append(v['topic'])
            source={**{k:row[k] for k in ('source_id','title','publisher','source_time','time_kind')},'provider':v['provider'],'topic':v['topic'],'checked_at':v['checked_at'],'first_available_at':receipt['first_available_at']}
            if source not in group['sources']: group['sources'].append(source)
    articles=sorted(groups.values(),key=lambda g:g['id'])
    status=('bounded_links' if articles else 'bounded_empty') if all(t['status'] in SUCCESS for t in topics) else 'partial'
    return {'status':status,'topics':topics,'articles':articles,'article_count':len(articles)}

def render(v):
    names={'company':'회사','sector':'업종','macro':'거시·금리','commodities':'원자재','politics':'정치·정책'}
    states={'links_available':'제한 검색에서 링크 확보','bounded_no_links':'제한 검색 결과 없음','partial':'일부 결과·개수 상한·제외 자료 있음','unavailable':'검색 실패·자료 없음','deferred':'공유 요청 제한으로 대기','skipped_after_failure':'같은 공급자 실패 후 추가 요청 보류','not_cached':'저장된 조회 결과 없음'}
    lines=[f"# {v['identity']['symbol']} 요청형 최근 뉴스",'',f"요청: {v['requested_at']} · 보고서 생성: {v['generated_at']}",f"검색 범위: {v['window']['start']} ~ {v['window']['end']} ({v['lookback_days']}일)",'',
        '종목 입력 후 필요한 기간만 검색했습니다. 기사 제목·주소·공급자 시간 표기만 저장하며 본문 수집과 AI 판독·투자 점수 반영은 수행하지 않습니다. 검색 후보의 기업 영향은 확인되지 않았습니다.','',
        '| 범위 | 상태 | 실제 조회 확인 시각 | 재사용 | 요청 기준 뒤 미검색 구간(초) |','|---|---|---|---|---:|']
    for t in v['topics']: lines.append(f"| {names[t['topic']]} | {states[t['status']]} | {text(t['checked_at'])} | {'예' if t['cache_reused'] else '아니오'} | {t['unsearched_tail_seconds']} |")
    lines += ['','재사용한 결과는 원래 조회·최초 보관 시각을 유지합니다. 검색 기준 뒤의 새 구간은 아직 확인하지 않았으며 현재 시각까지 검색 완료했다고 표시하지 않습니다. 공급자의 수집 지연과 제한 검색 누락은 별도로 남을 수 있습니다.','',
        '## 최근 시간 표기 순 기사 링크','',
        '회사 기사의 시간은 공급자의 게시 시각 표기이며 독립 확인한 최초 공개 시각이 아닙니다. GDELT 시간은 기사 관측 시각이며 발행일·사건 시각을 보장하지 않습니다. 아래 순서는 시간 표기 기준이며 중요도·호재 순위가 아닙니다.','']
    ordered=sorted(v['articles'],key=lambda a:max(as_utc(s['source_time']) for s in a['sources']),reverse=True)
    for a in ordered:
        source=max(a['sources'],key=lambda s:as_utc(s['source_time'])); kind='공급자 게시 표기' if source['time_kind']=='provider_publication_assertion' else '색인 관측'
        lines += [f"- {link(a['url'],text(a['title']))}",f"  - {', '.join(names[t] for t in a['topics'])} · {kind}: {source['source_time']} · 실제 조회: {source['checked_at']}"]
    if not ordered: lines.append('확보한 링크가 없습니다. 검색 실패·미조회는 관련 뉴스가 없다는 뜻이 아닙니다.')
    lines += ['','같은 URL의 중복만 통합했습니다. 다른 URL이 같은 사건인지, 기사 사실·방향·기업 영향·선반영 여부는 인증하지 않습니다. 정식 추천도와 뉴스 영향 점수는 보류합니다.','',
        '출처: Massive ticker news / The GDELT Project (https://www.gdeltproject.org/). 메타데이터 사용 범위 내 개인용 자료입니다.','']
    return '\n'.join(lines)

def run(ticker,days,baseline,*,client,refresh=False,offline=False,illustrative=False,clock=now,sleep=time.sleep,store=None):
    if refresh and offline: raise ValueError('OFFLINE_AND_REFRESH_CONFLICT')
    proof=scope.check(baseline); hashes=methods(); settings=cache.policy(); requested=clock(); profile=resolve(ticker,requested,illustrative=illustrative)
    if client.record['is_illustrative'] is not illustrative: raise ValueError('ON_DEMAND_CLIENT_NAMESPACE_MISMATCH')
    if not illustrative and (client.baseline.resolve()!=Path(baseline).resolve() or client.baseline_sha256!=proof['baseline_sha256']): raise ValueError('ON_DEMAND_CLIENT_BASELINE_MISMATCH')
    items=plan(profile,days,requested,settings,hashes); before=(client.massive_requests,client.gdelt_requests); results=[]; stopped=False
    with store or cache.Store(illustrative=illustrative,clock=clock) as storage:
        storage.check_report_capacity(); removed=storage.prune()
        for item in items:
            scope.check(baseline,expected_sha256=proof['baseline_sha256'])
            if methods()!=hashes: raise ValueError('ON_DEMAND_METHOD_CHANGED_DURING_RUN')
            if item['provider']=='gdelt_project' and stopped:
                value=base_value(item); value.update(status='skipped_after_failure',reason_code='STOP_AFTER_GDELT_FAILURE')
                results.append({'key':item['key'],'cache_id':None,'first_available_at':None,'cache_reused':False,'value':value}); continue
            try:
                if not illustrative: news_archive.require_metadata_rights({item['provider']},clock())
                entry=storage.get(item['key'],refresh=refresh)
                reused=entry is not None
                if entry is None:
                    if offline:
                        value=base_value(item); value.update(status='not_cached',reason_code='OFFLINE_NO_FRESH_CACHE')
                        results.append({'key':item['key'],'cache_id':None,'first_available_at':None,'cache_reused':False,'value':value}); continue
                    value=fetch(item,profile,client,clock); validate_value(value,item,clock()); entry=storage.put(item['key'],value)
                validate_value(entry['value'],item,clock())
                result={k:entry[k] for k in ('key','cache_id','first_available_at','value')}; result['cache_reused']=reused
            except RightsError:
                value=base_value(item); value.update(status='unavailable',reason_code='SOURCE_RIGHTS_NOT_CLEARED')
                result={'key':item['key'],'cache_id':None,'first_available_at':None,'cache_reused':False,'value':value}
            results.append(result)
            if item['provider']=='gdelt_project' and result['value']['status'] not in SUCCESS|{'partial'}: stopped=True
            if client.massive_requests-before[0]+client.gdelt_requests-before[1]>settings['maximum_http_requests_per_run']: raise ValueError('ON_DEMAND_HTTP_CAP_EXCEEDED')
        available=[r['first_available_at'] for r in results if r['first_available_at']]
        if available:
            due=max(as_utc(t) for t in available).replace(microsecond=0)+timedelta(seconds=1); delay=(due-as_utc(clock())).total_seconds()
            if delay>0: sleep(min(delay+.01,1.05))
            if as_utc(clock()).replace(microsecond=0)<=max(as_utc(t) for t in available): raise ValueError('NEWS_RECEIPT_NOT_BEFORE_REPORT_CUTOFF')
        at=clock(); value={'contract':'on-demand-news-v1','run_id':uuid.uuid4().hex,'identity':{k:profile[k] for k in ('symbol','cik','issuer_name')},'profile_binding':profile['binding'],'is_illustrative':illustrative,
            'requested_at':requested,'generated_at':at,'lookback_days':days,'window':{'start':items[0]['window_start'],'end':items[0]['window_end']},'baseline_path':str(Path(baseline).resolve()),'baseline_sha256':proof['baseline_sha256'],
            'method_hashes':hashes,'query_receipts':results,**assemble(results,items[0]['window_start'],items[0]['window_end']),
            'requests':{'massive':client.massive_requests-before[0],'gdelt':client.gdelt_requests-before[1]},'cache_hits':sum(r['cache_reused'] for r in results),'cache_evictions':removed,
            'offline':offline,'publisher_body_fetched':False,'external_inference_calls':0,'model_training_performed':False,'numeric_news_impact':None,'released_score':None,'realtime_guaranteed':False}
        value['record_sha256']=stable_hash(value)
        validate_packet(value)
        folder=storage.save(value,render(value)); scope.check(baseline,expected_sha256=proof['baseline_sha256']); return value,folder

def validate_packet(v):
    cache.metadata_only(v); jsonschema.validate(v,read(ROOT/'extensions/ondemand_contract.schema.json'))
    if v['method_hashes']!=methods() or stable_hash({k:x for k,x in v.items() if k!='record_sha256'})!=v['record_sha256']: raise ValueError('ON_DEMAND_PACKET_CHANGED')
    p=resolve(v['identity']['symbol'],v['generated_at'],illustrative=v['is_illustrative'])
    if v['identity']!={k:p[k] for k in ('symbol','cik','issuer_name')} or v['profile_binding']!=p['binding']: raise ValueError('ON_DEMAND_ISSUER_BINDING_CHANGED')
    items=plan(p,v['lookback_days'],v['requested_at'],cache.policy(),methods())
    if v['window']!={'start':items[0]['window_start'],'end':items[0]['window_end']} or as_utc(v['requested_at'])>as_utc(v['generated_at']): raise ValueError('ON_DEMAND_REQUEST_WINDOW_CHANGED')
    for receipt,item in zip(v['query_receipts'],items):
        if receipt['key']!=item['key'] or receipt['cache_id'] not in (None,stable_hash(item['key'])): raise ValueError('ON_DEMAND_RECEIPT_BINDING_CHANGED')
        validate_value(receipt['value'],item,v['generated_at'])
        if receipt['cache_id'] is None and (receipt['first_available_at'] is not None or receipt['cache_reused'] or receipt['value']['articles']): raise ValueError('ON_DEMAND_UNCOMMITTED_RECEIPT')
        if receipt['cache_id'] is not None and (receipt['first_available_at'] is None or receipt['value']['checked_at'] is None): raise ValueError('ON_DEMAND_RECEIPT_TIME_MISSING')
        if receipt['first_available_at'] is not None and not as_utc(receipt['value']['checked_at'])<=as_utc(receipt['first_available_at'])<as_utc(v['generated_at']).replace(microsecond=0): raise ValueError('ON_DEMAND_RECEIPT_AVAILABILITY_CHANGED')
    rebuilt=assemble(v['query_receipts'],v['window']['start'],v['window']['end'])
    if any(v[k]!=x for k,x in rebuilt.items()) or sum(v['requests'].values())>5 or v['offline'] and sum(v['requests'].values()) or v['cache_hits']!=sum(r['cache_reused'] for r in v['query_receipts']): raise ValueError('ON_DEMAND_OUTPUT_REPLAY_CHANGED')

def validate(folder):
    folder=Path(folder).resolve()
    if not folder.is_relative_to((ROOT/'reports/private/on_demand_news').resolve()): raise ValueError('PRIVATE_NEWS_RUN_REQUIRED')
    v=read(folder/'PACKET.json')
    if folder.name!=v['run_id'] or folder.parent.parent.name!=('illustrative' if v['is_illustrative'] else 'real'): raise ValueError('ON_DEMAND_RUN_NAMESPACE_CHANGED')
    historical_check(v['baseline_path'],expected_sha256=v['baseline_sha256'])
    if not v['is_illustrative']: news_archive.require_metadata_rights({r['value']['provider'] for r in v['query_receipts'] if r['value']['articles']},now())
    validate_packet(v); files=read(folder/'FILES.json')
    if set(files)!={'PACKET.json','REPORT.ko.md'} or any(digest(folder/n)!=sha for n,sha in files.items()) or render(v)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8'): raise ValueError('ON_DEMAND_REPORT_FILES_CHANGED')
    return v

def _main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--ticker'); p.add_argument('--days',type=int,default=7); p.add_argument('--baseline',type=Path); p.add_argument('--refresh',action='store_true'); p.add_argument('--offline',action='store_true'); p.add_argument('--validate',type=Path); a=p.parse_args(argv)
    if a.validate: v=validate(a.validate); print(json.dumps({'status':'on_demand_report_valid','articles':v['article_count']})); return
    if not a.ticker or not a.baseline: p.error('--ticker and --baseline required')
    from research.learned_research import latest_training_snapshot
    _,entitlement=latest_training_snapshot(); client=ondemand_transport.Client(read(entitlement),os.environ.get('MASSIVE_API_KEY',''),a.baseline)
    v,folder=run(a.ticker.upper(),a.days,a.baseline,client=client,refresh=a.refresh,offline=a.offline); validate(folder)
    print(json.dumps({'status':v['status'],'symbol':v['identity']['symbol'],'days':v['lookback_days'],'articles':v['article_count'],'requests':v['requests'],'cache_hits':v['cache_hits'],'folder':str(folder)}))

def main(argv=None):
    try: _main(argv); return 0
    except LockBusy: print(json.dumps({'status':'another_on_demand_request_running'})); return 2
    except Exception: print(json.dumps({'status':'on_demand_request_failed','reason_code':'INPUT_RIGHTS_STORAGE_OR_SOURCE_VALIDATION_FAILED'})); return 1

if __name__=='__main__': raise SystemExit(main())
