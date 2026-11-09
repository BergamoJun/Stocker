"""ILLUSTRATIVE - immutable composition, identity and release boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from extensions import impact_report as report, impact_quality as quality, issuer_impact as impact
from extensions.tests.test_impact_critic import CorrectBackend
from research.local_files import atomic_json
from research.validation import stable_hash


def setup(tmp_path,monkeypatch,*,passed=True):
    v=deepcopy(quality.cases()[0]['input']); at=v['as_of']; ai={'identity':v['identity'],'is_illustrative':True,'as_of':'2026-11-08T00:00:00Z'}
    news={'status':'partial','generated_at':'2026-11-09T12:00:00Z','window':{'start':'2026-11-02T12:00:00Z','end':'2026-11-09T12:00:00Z'},'topics':[{'topic':'company','status':'unavailable','unsearched_tail_seconds':None}],'article_count':1,'articles':[{'title':'ILLUSTRATIVE metadata must never reach model','url':'https://example.org/link'}]}
    row={'input':v,'source_url':'https://www.eia.gov/example','semantic_record_sha256':'a'*64,'semantic_cache_path':'ILLUSTRATIVE'}
    q=quality.evaluate(CorrectBackend(),clock=lambda:'2026-11-09T13:00:00Z')
    if not passed:
        q['details'][6]['critic_raw']={'verdict':'conditional_only','reason':'exposure_unverified'}; q['counts']=quality.summarize(q['details']); q['decision']='quality_gate_failed'; q['record_sha256']=stable_hash({k:x for k,x in q.items() if k!='record_sha256'})
    paths=[]
    for name,data in [('input/AI_INPUT.json',ai),('news/PACKET.json',news),('quality.json',q)]:
        p=tmp_path/'reports/private'/name; atomic_json(p,data); paths.append(p)
    monkeypatch.setattr(report,'ROOT',tmp_path)
    monkeypatch.setattr(report.scope,'check',lambda *a,**k:{'baseline_sha256':'b'*64})
    monkeypatch.setattr(report,'historical_check',lambda *a,**k:{})
    monkeypatch.setattr(report.semantic_research,'quality_gate',lambda *a,**k:{})
    monkeypatch.setattr(report,'context',lambda *a,**k:(ai,news,[row],{'selected_facts':4,'financial_gaps':[]}))
    return paths,at


def test_saved_report_replays_and_preserves_metadata_separation(tmp_path,monkeypatch):
    paths,at=setup(tmp_path,monkeypatch)
    class Backend(CorrectBackend):
        def ask(self,msg,spec):
            assert 'metadata must never reach model' not in msg[1]['content']
            return super().ask(msg,spec)
    v,folder=report.save(paths[0].parent,paths[1].parent,'baseline',paths[2],backend=Backend(),clock=lambda:at)
    assert report.validate(folder)==v
    assert v['released_score'] is None and v['released_price_plan'] is None
    assert '독립 모델' in (folder/'REPORT.ko.md').read_text(encoding='utf-8')
    (folder/'REPORT.ko.md').write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='FILES_CHANGED'): report.validate(folder)


def test_failed_quality_produces_withheld_report_without_model(tmp_path,monkeypatch):
    paths,at=setup(tmp_path,monkeypatch,passed=False)
    class Never:
        def ask(self,*a): raise AssertionError('quality failure must prevent inference')
    v,folder=report.save(paths[0].parent,paths[1].parent,'baseline',paths[2],backend=Never(),clock=lambda:at)
    assert v['cards'][0]['status']=='quality_withheld'
    assert report.validate(folder)==v


def test_actual_namespace_alias_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(report,'ROOT',tmp_path/'unresolved/../alias')
    with pytest.raises(ValueError,match='ALIAS'): report.root_for(False)


def test_cross_issuer_news_is_not_attached(monkeypatch):
    v=quality.cases()[0]['input']; ai={'identity':v['identity'],'is_illustrative':True}
    n={'identity':{'cik':'0000000002','symbol':'OTHER'},'is_illustrative':True}
    monkeypatch.setattr(report.analysis_contract,'export_ai',lambda *a,**k:ai)
    monkeypatch.setattr(report.ondemand_news,'validate',lambda *a:n)
    with pytest.raises(ValueError,match='ISSUER_MISMATCH'): report.context('input','news',v['as_of'],7)


def test_model_unavailable_retains_partial_report_not_neutral_score():
    c=quality.cases()[0]
    r=report.assess({'input':c['input']},None,True)
    assert r['status']=='inference_deferred' and r['proposal'] is None
