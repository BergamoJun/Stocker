"""Immutable Korean semantic/eligibility reports and source/trust replay."""
from pathlib import Path
import uuid
from research.sec_transport import now
from research.sec_report import ROOT
from research.validation import digest
from . import read,write_once,seal,verify_seal,discovery,bridge,reviews


def render(bundle):
    r=bundle['adapter'];o=bundle['bridge']['output']
    lines=['# SEC EvidenceAdapter v1.1 의미 검증 SHADOW 보고서','',
        'ILLUSTRATIVE — NOT LIVE DATA' if r['is_illustrative'] else '실제 저장 SEC 자료의 검증 보고서입니다. 독립 계약 검토가 없는 금액은 생성하지 않습니다.',
        f"기업: {r['identity']['ticker']} / CIK {r['identity']['cik']}",f"기준시각: {r['as_of']}",
        f"계산 기간: {r['eligibility_window']['start']} ~ {r['eligibility_window']['end']} (종료일 제외)",
        f"상태: {r['status']}; 과거 사실 검증 {len(r['historical_evidence'])}; 현재 계산 적격 {len(r['verified_exposures'])}",
        f"ImpactEngine: {o['status']}; 금액: {o['amount'] if o['amount'] is not None else 'null (보류)'}",'',
        '## 원문·주장·관계 검증','']
    for row in r['semantic_assessments']:
        lines += [f"- {row['instrument_id']}: {row['semantic']['status']}; calculation_eligible={row['eligibility']['calculation_eligible']}"]
        lines += [f"  - {i['code']}: {i['detail']}" for i in row['semantic']['issues']]
        lines += ['  - '+x for x in row['eligibility']['eligibility_reason']]
    lines+=['','## 검토 대기·부족한 근거','']+[f"- {x['field']}: {x['reason']}" for x in r['missing_evidence']]
    lines+=['','## 원문과 방법','']+[f"- {s['accession']} / {s['form']} / SHA-256 {s['original_sha256']} / {s['source_url']}" for s in r['sources']]
    lines+=['','## 한계','']+['- '+x for x in r['limitations']]
    lines+=['','SEMANTIC_RULE_VALIDATED는 지원 문장과 관계 규칙의 통과이며 사람의 독립 판정 또는 모든 금융 계약의 의미 정확성 입증이 아닙니다.',
        'SHADOW 유지. 실제 시장 의미 정확도 NOT_MEASURED. 투자 점수·목표가·거래 계획은 null. 신규 SEC 요청·모델·유료 API 호출 0.']
    return '\n'.join(lines)+'\n'


def queue(result):
    return {'contract':'sec-contract-semantic-review-queue-v1.1','actual_human_reviews_completed':0,
        'status':'HUMAN_REVIEW_REQUIRED','no_model_input':True,
        'instruction':'Independent source review is required. Semantic support is independently reparsed; reviewer semantic labels and booleans cannot approve missing relationships.',
        'sources':result['sources'],'assessments':result['semantic_assessments'],
        'draft_manifests':[reviews.template(s['binding'],illustrative=result['is_illustrative']) for s in result['sources'] if s['kind']=='document']}


def run(cik,ticker,as_of,output_root,*,trust=None,event_input=None,instrument_id=None,created_at=None,**options):
    if not Path(output_root).resolve().is_relative_to((ROOT/'reports/private').resolve()):raise ValueError('PRIVATE_REPORT_REQUIRED')
    trust=trust or reviews.empty_trust(options.get('illustrative',False));at=created_at or now()
    if event_input is not None:
        options['calculation_window']={'start':event_input['context']['calculation_start'],'end':event_input['context']['calculation_end']}
        options['event_effective']=event_input['event']['effective_date']
    r=discovery.build(cik,ticker,as_of,trust=trust,**options)
    b=bridge.calculate(r,trust,event_input=event_input,instrument_id=instrument_id,created_at=at)
    value=seal({'contract':'sec-evidence-semantic-shadow-bundle-v1.1','adapter':r,'bridge':b,'trust':trust,
        'event_input':event_input,'instrument_id':instrument_id,'created_at':at})
    folder=Path(output_root)/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=False)
    write_once(folder/'ADAPTER.json',value);write_once(folder/'REVIEW_QUEUE.private.json',queue(r))
    with (folder/'REPORT.ko.md').open('x',encoding='utf-8') as stream:stream.write(render(value))
    write_once(folder/'FILES.json',{p.name:digest(p) for p in folder.iterdir()})
    return folder


def validate(folder,*,expected_trust=None):
    folder=Path(folder);files=read(folder/'FILES.json')
    expected={'ADAPTER.json','REVIEW_QUEUE.private.json','REPORT.ko.md'}
    if set(files)!=expected or {p.name for p in folder.iterdir()}!=expected|{'FILES.json'} or any(digest(folder/n)!=h for n,h in files.items()):raise ValueError('SEMANTIC_REPORT_FILES_CHANGED')
    b=read(folder/'ADAPTER.json');verify_seal(b);r=b['adapter']
    if not r['is_illustrative'] and (b['trust']['trusted_review_hashes'] or b['trust']['trusted_event_review_hashes']) and expected_trust!=b['trust']:
        raise ValueError('REAL_REPLAY_REQUIRES_EXTERNAL_PINNED_OWNER_TRUST')
    discovery.revalidate(r,b['trust'])
    if bridge.calculate(r,b['trust'],event_input=b['event_input'],instrument_id=b['instrument_id'],created_at=b['created_at'])!=b['bridge']:
        raise ValueError('SEMANTIC_IMPACT_REPLAY_CHANGED')
    if render(b)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8') or queue(r)!=read(folder/'REVIEW_QUEUE.private.json'):
        raise ValueError('SEMANTIC_REPORT_RENDER_CHANGED')
    return {'status':'VALID','calculation_eligible':len(r['verified_exposures']),
        'historical_evidence':len(r['historical_evidence']),'amount':b['bridge']['output']['amount'],'new_model_calls':0}
