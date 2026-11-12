"""Immutable Korean adapter reports, review drafts and fresh-source replay."""
from copy import deepcopy
from pathlib import Path
import json
import uuid
from research.sec_transport import now
from research.sec_report import ROOT
from research.validation import digest,stable_hash
from . import read,write_once,seal,verify_seal,methods,discovery,bridge,reviews,contract


def queue(result):
    drafts={s['incarnation_id']:reviews.template(s['binding'],illustrative=result['is_illustrative'])
        for s in result['sources'] if s['kind']=='document'}
    return {'contract':'sec-contract-offline-review-queue-v1','status':'REVIEW_REQUIRED','actual_human_reviews_completed':0,
        'instruction':'Two independent humans review source/contract facts separately. Drafts are not approvals. Resolve disagreements outside the model; do not copy another primary review.',
        'sources':result['sources'],'candidates':result['candidates'],'draft_manifests':list(drafts.values()),
        'required_terms_schema':contract.TERMS,'no_model_input':True}


def render(bundle):
    r=bundle['adapter'];b=bundle['bridge'];v=b['output'];s=r['statistics']
    lines=['# SEC EvidenceAdapter v1 SHADOW 보고서','',
        'ILLUSTRATIVE — NOT LIVE DATA' if r['is_illustrative'] else '실제 저장 SEC 공시의 후보·근거 검사입니다. 실제 손익 영향의 입증이 아닙니다.',
        f"기업: {r['identity']['ticker']} / CIK {r['identity']['cik']}",f"기준시각: {r['as_of']}",
        f"후보 상태: {r['status']} / 후보 {len(r['candidates'])} / 독립 검토 계약 {len(r['verified_exposures'])}",
        f"ImpactEngine: {v['status']} / 금액 {v['amount'] if v['amount'] is not None else 'null (보류)'}",
        '', '## 재사용한 재무 검토','',
        '기존 숫자 검토와 독립 계약 노출 검토를 구분합니다. assistant 검토를 사람의 독립 승인으로 바꾸지 않습니다.']
    for f in r['reviewed_financial_facts']:
        lines.append(f"- {f['accession']} / {f['metric']} / {f['fact']['value']} {f['fact']['unit']} / {f['scope']} / {f['reviewer_kind']}")
    lines+=['','## 검토한 원문과 변환','']
    for src in r['sources']:
        lines.append(f"- {src['accession']} {src['form']} / {src['source_url']} / 원본 SHA-256 {src['original_sha256']} / 추출 텍스트 SHA-256 {src['derived_text_sha256']} / 최초 가용 {src['available_at']} / incarnation {src['incarnation_id']}")
    lines+=['','## 후보와 위치','', '| 종류 | 후보 의미 | 값·단위 | 상태 | locator |','|---|---|---|---|---|']
    for c in r['candidates']:
        lines.append(f"| {c['origin']} / {c['taxonomy']} | {c['metric']} | {c['value']} {c['unit']} | {c['status']} | {json.dumps(c['locator'],ensure_ascii=False)} |")
    lines+=['','## 부족한 계약 근거','']+[f"- {m['field']}: {m['reason']}" for m in r['missing_evidence']]
    lines+=['','## 충돌·stale·검토 대기','']+[f"- {c['code']}: {c['resolution']}" for c in r['conflicts']]
    if b['selection_issue']:lines.append('- '+b['selection_issue'])
    lines += [f"- {i['code']}: {i['detail']}" for i in v['issues']]
    lines+=['','## 비용·요청·범위','',f"새 SEC 요청 {s['network_requests']}, 파싱 캐시 hit {s['parse_cache_hits']}, miss {s['parse_cache_misses']}, 선택 공시 {s['filings_selected']}/{s['filings_available']}, cap으로 빠진 후보 {s['candidate_cap_omitted']}.",
        '유료 API·모델 호출 0. 일반 사용자 요청은 자동으로 부분 보고서를 생성하며 신규 계약의 독립 검토만 별도 운영자가 수행합니다.',
        '검토 완료는 자동 선언하지 않습니다. machine binding certificate는 사람의 새로운 검토가 아닙니다.',
        '', '## 한계','']+[f'- {x}' for x in r['limitations']]
    lines+=['','주가 수익 예측·목표가·점수·매수/매도·추천 배분은 null, ResearchEngine은 SHADOW, 시장 의미 정확도는 NOT_MEASURED입니다.']
    return '\n'.join(lines)+'\n'


def run(cik,ticker,as_of,output_root,*,trust=None,event_input=None,instrument_id=None,created_at=None,**options):
    trust=trust or reviews.empty_trust(options.get('illustrative',False));at=created_at or now()
    r=discovery.build(cik,ticker,as_of,trust=trust,**options)
    b=bridge.calculate(r,trust,event_input=event_input,instrument_id=instrument_id,created_at=at)
    value=seal({'contract':'sec-evidence-shadow-bundle-v1','adapter':r,'bridge':b,'trust':trust,
        'config':{'cik':cik,'ticker':ticker,'as_of':as_of,**{k:str(v) if isinstance(v,Path) else v for k,v in options.items()},
            'event_input':deepcopy(event_input),'instrument_id':instrument_id,'created_at':at}})
    folder=Path(output_root)/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=False)
    write_once(folder/'ADAPTER.json',value);write_once(folder/'OFFLINE_REVIEW_QUEUE.private.json',queue(r))
    with (folder/'REPORT.ko.md').open('x',encoding='utf-8') as stream:stream.write(render(value))
    write_once(folder/'FILES.json',{p.name:digest(p) for p in folder.iterdir()})
    return folder


def semantic(value):
    value=deepcopy(value);value.pop('record_sha256',None)
    for name in ['parse_cache_hits','parse_cache_misses','parse_cache_capacity_skips']:value['statistics'].pop(name,None)
    return value


def validate(folder,*,expected_trust=None):
    folder=Path(folder);files=read(folder/'FILES.json')
    if set(files)!={'ADAPTER.json','OFFLINE_REVIEW_QUEUE.private.json','REPORT.ko.md'} or {p.name for p in folder.iterdir()}!=set(files)|{'FILES.json'} or any(digest(folder/n)!=h for n,h in files.items()):raise ValueError('ADAPTER_REPORT_FILES_CHANGED')
    bundle=read(folder/'ADAPTER.json');verify_seal(bundle);r=bundle['adapter'];verify_seal(r);contract.validate(r,'result')
    nonempty=bundle['trust']['trusted_review_hashes'] or bundle['trust']['trusted_event_review_hashes']
    if not r['is_illustrative'] and nonempty and expected_trust!=bundle['trust']:
        raise ValueError('REAL_REPLAY_REQUIRES_EXTERNAL_PINNED_OWNER_TRUST')
    options={k:v for k,v in bundle['config'].items() if k not in {'event_input','instrument_id','created_at'}}
    options.pop('fresh',None)
    rebuilt=discovery.build(**options,trust=bundle['trust'],fresh=True)
    if semantic(rebuilt)!=semantic(r):raise ValueError('ADAPTER_FRESH_SOURCE_REPLAY_CHANGED')
    recalculated=bridge.calculate(r,bundle['trust'],event_input=bundle['config']['event_input'],instrument_id=bundle['config']['instrument_id'],created_at=bundle['config']['created_at'])
    if recalculated!=bundle['bridge']:raise ValueError('ADAPTER_IMPACT_REPLAY_CHANGED')
    if queue(r)!=read(folder/'OFFLINE_REVIEW_QUEUE.private.json') or render(bundle)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8'):raise ValueError('ADAPTER_QUEUE_OR_RENDER_CHANGED')
    return {'status':'VALID','candidate_count':len(r['candidates']),'verified_contracts':len(r['verified_exposures']),'impact_status':recalculated['output']['status'],'new_model_calls':0}
