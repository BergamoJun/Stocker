"""ILLUSTRATIVE - request windows, point-in-time copies and truthful coverage."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import pytest
from research.local_files import atomic_json
from research.news_transport import NewsRequestDeferred
from research.validation import stable_hash
from extensions import ondemand_news as app,ondemand_cache as cache
from extensions.tests.test_ondemand_cache import Clock

@pytest.fixture
def case(tmp_path,monkeypatch):
    hashes=app.methods(); root=tmp_path/'project'; monkeypatch.setattr(app,'ROOT',root); monkeypatch.setattr(cache,'ROOT',root); monkeypatch.setattr(app,'methods',lambda:hashes)
    schema=Path(app.__file__).with_name('ondemand_contract.schema.json').read_text(encoding='utf-8'); target=root/'extensions/ondemand_contract.schema.json'; target.parent.mkdir(parents=True); target.write_text(schema,encoding='utf-8')
    monkeypatch.setattr(app.scope,'check',lambda *a,**k:{'baseline_sha256':'a'*64}); monkeypatch.setattr(app,'historical_check',lambda *a,**k:{})
    clock=Clock(); profiles={'DEMO':{'symbol':'DEMO','cik':'0000001001','issuer_name':'ILLUSTRATIVE','sector_query':'"ILLUSTRATIVE sector"','binding':{'receipt_id':'ILLUSTRATIVE-1'}},'OTHER':{'symbol':'OTHER','cik':'0000001002','issuer_name':'ILLUSTRATIVE 2','sector_query':'"ILLUSTRATIVE sector"','binding':{'receipt_id':'ILLUSTRATIVE-2'}}}
    def resolve(ticker,*a,**k):
        if ticker not in profiles: raise ValueError('UNSUPPORTED_ON_DEMAND_TICKER')
        return deepcopy(profiles[ticker])
    monkeypatch.setattr(app,'resolve',resolve)
    class Client:
        def __init__(self): self.record={'is_illustrative':True}; self.massive_requests=self.gdelt_requests=0; self.fail=False; self.empty=False; self.limited=False; self.windows=[]
        def massive(self,ticker,start,end):
            self.massive_requests+=1; self.windows.append((start,end)); at=(app.as_utc(end)-timedelta(hours=1)).isoformat()
            rows=[] if self.empty else [{'tickers':[ticker],'title':'ILLUSTRATIVE title','article_url':'https://news.example/article?a=1&utm_source=fixture','publisher':{'name':'ILLUSTRATIVE'},'published_utc':at,'description':'FORBIDDEN BODY','insights':[{'sentiment':'positive'}]}]
            raw=json.dumps({'status':'OK','results':rows,'next_url':'https://unused.example/' if self.limited else None}).encode()
            return {'raw':raw,'retrieved_at':clock(),'raw_sha256':hashlib.sha256(raw).hexdigest()}
        def gdelt_window(self,query,start,end):
            self.gdelt_requests+=1; self.windows.append((start,end))
            if self.fail: raise NewsRequestDeferred((clock.at+timedelta(minutes=15)).isoformat())
            at=(app.as_utc(end)-timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')
            return {'articles':[] if self.empty else [{'url':'https://news.example/article?a=1','title':'ILLUSTRATIVE title','seendate':at,'body':'FORBIDDEN BODY'}]}
    client=Client(); baseline=tmp_path/'baseline.json'; atomic_json(baseline,{'ILLUSTRATIVE':True})
    def run(days=7,**kwargs): return app.run('DEMO',days,baseline,client=client,illustrative=True,clock=clock,sleep=clock.pause,**kwargs)
    return {'root':root,'clock':clock,'client':client,'run':run,'baseline':baseline,'profiles':profiles,'monkeypatch':monkeypatch}

def golden(): return json.loads(Path(__file__).with_name('ondemand_golden.json').read_text(encoding='utf-8'))

def test_golden_explicit_window_metadata_only_and_url_dedup(case):
    v,folder=case['run'](); g=golden()
    for k,x in g['successful_fixture'].items(): assert v[k]==x
    for k,x in g['forbidden_promotions'].items(): assert v[k]==x
    assert [t['topic'] for t in v['topics']]==g['topics']
    assert len(v['articles'][0]['sources'])==5
    assert {s['time_kind'] for s in v['articles'][0]['sources']}=={g['company_time_kind'],g['gdelt_time_kind']}
    assert all(app.as_utc(end)-app.as_utc(start)==timedelta(days=7) for start,end in case['client'].windows)
    assert app.validate(folder)==v
    stored=(folder/'PACKET.json').read_text(encoding='utf-8')
    assert 'FORBIDDEN BODY' not in stored and 'sentiment' not in stored

def test_reuse_keeps_original_observation_and_reports_unsearched_tail(case):
    first,_=case['run'](); case['clock'].pause(60); second,_=case['run']()
    for k,x in golden()['reused_fixture'].items(): assert second[k]==x
    assert [q['first_available_at'] for q in first['query_receipts']]==[q['first_available_at'] for q in second['query_receipts']]
    assert all(t['unsearched_tail_seconds']>=60 for t in second['topics'])
    assert first['query_receipts'][0]['value']['checked_at']==second['query_receipts'][0]['value']['checked_at']

def test_days_in_cache_key_and_new_period_cannot_reuse_three_day_result(case):
    case['run'](3); v,_=case['run'](7)
    assert v['cache_hits']==0 and v['requests']=={'massive':1,'gdelt':4}
    assert len(case['client'].windows)==10

def test_cross_issuer_reuses_general_candidates_not_company_binding(case):
    case['run'](); case['clock'].pause(60)
    v,_=app.run('OTHER',7,case['baseline'],client=case['client'],illustrative=True,clock=case['clock'],sleep=case['clock'].pause)
    assert v['cache_hits']==4 and v['requests']=={'massive':1,'gdelt':0}
    assert v['identity']['cik']=='0000001002' and v['profile_binding']['receipt_id']=='ILLUSTRATIVE-2'

def test_first_gdelt_failure_stops_later_queries_and_failure_is_cached(case):
    case['client'].fail=True; v,_=case['run'](); expected=golden()['failed_fixture']
    assert v['status']==expected['status'] and v['requests']=={'massive':1,'gdelt':1}
    assert [t['status'] for t in v['topics']]==[expected['company_status'],expected['sector_status']]+[expected['later_status']]*3
    second,_=case['run'](); assert second['requests']=={'massive':0,'gdelt':0} and second['cache_hits']==2

def test_empty_bounded_result_differs_from_failure_and_cap_is_partial(case):
    case['client'].empty=True; v,_=case['run'](); assert v['status']=='bounded_empty' and v['article_count']==0
    case['client'].empty=False; case['client'].limited=True
    v,_=case['run'](refresh=True); assert v['status']=='partial' and v['topics'][0]['limit_reached']

def test_offline_cold_cache_and_force_refresh(case):
    v,_=case['run'](offline=True); assert v['requests']=={'massive':0,'gdelt':0} and all(t['status']=='not_cached' for t in v['topics'])
    case['run'](); v,_=case['run'](refresh=True); assert v['cache_hits']==0 and v['requests']=={'massive':1,'gdelt':4}
    with pytest.raises(ValueError,match='CONFLICT'): case['run'](refresh=True,offline=True)

@pytest.mark.parametrize('days',[0,31,True])
def test_invalid_window_does_not_call_provider(case,days):
    with pytest.raises(ValueError,match='LOOKBACK'): case['run'](days)
    assert case['client'].massive_requests+case['client'].gdelt_requests==0

def test_unknown_issuer_and_namespace_mismatch_stop_before_requests(case):
    with pytest.raises(ValueError,match='UNSUPPORTED'): app.run('UNKNOWN',7,case['baseline'],client=case['client'],illustrative=True,clock=case['clock'])
    case['client'].record['is_illustrative']=False
    with pytest.raises(ValueError,match='NAMESPACE'): case['run']()
    assert case['client'].massive_requests==0

def test_report_survives_cache_expiry_eviction_and_preserves_receipt(case):
    v,folder=case['run'](); case['clock'].pause(172801)
    with cache.Store(illustrative=True,clock=case['clock']) as storage: storage.prune(); assert storage.cache_files()==[]
    assert app.validate(folder)==v

def test_output_tampering_released_score_and_wrong_time_kind_are_rejected(case):
    v,folder=case['run'](); modified=deepcopy(v); modified['released_score']=50; modified['record_sha256']=stable_hash({k:x for k,x in modified.items() if k!='record_sha256'})
    with pytest.raises(Exception): app.validate_packet(modified)
    modified=deepcopy(v); modified['query_receipts'][1]['value']['articles'][0]['time_kind']='provider_publication_assertion'; modified['record_sha256']=stable_hash({k:x for k,x in modified.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='SOURCE_ID'): app.validate_packet(modified)
    (folder/'REPORT.ko.md').write_text('ILLUSTRATIVE changed',encoding='utf-8')
    with pytest.raises(ValueError,match='FILES'): app.validate(folder)

def test_report_quota_blocks_network_before_request(case):
    settings={**cache.policy(),'maximum_reports':1}; storage=cache.Store(illustrative=True,settings=settings,clock=case['clock'])
    case['run'](store=storage); count=case['client'].massive_requests+case['client'].gdelt_requests
    with pytest.raises(ValueError,match='STORAGE_CAP'): case['run'](store=cache.Store(illustrative=True,settings=settings,clock=case['clock']))
    assert count==case['client'].massive_requests+case['client'].gdelt_requests

def test_request_window_and_same_second_receipt_cannot_be_backdated(case):
    v,_=case['run'](); modified=deepcopy(v); modified['lookback_days']=3; modified['record_sha256']=stable_hash({k:x for k,x in modified.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='WINDOW'): app.validate_packet(modified)
    modified=deepcopy(v); modified['generated_at']=modified['query_receipts'][0]['first_available_at']; modified['record_sha256']=stable_hash({k:x for k,x in modified.items() if k!='record_sha256'})
    with pytest.raises(ValueError): app.validate_packet(modified)

def test_cli_redacts_private_error_details(monkeypatch,capsys):
    def fail(argv): raise ValueError('ILLUSTRATIVE private headline or secret')
    monkeypatch.setattr(app,'_main',fail)
    assert app.main([])==1 and 'private' not in capsys.readouterr().out
