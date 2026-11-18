"""Official immutable SHADOW reports; embedded policy is never root authority."""
from pathlib import Path
import uuid
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest
from extensions.sec_evidence_v1_1.report import queue as review_queue
from . import VERIFIER,Rejected,read,write_once,seal,verify_seal,methods,facade
from .owner import context


def render(bundle):
    q=bundle['request'];d=bundle['decision'];a=d['authorization']
    lines=['# SEC 공식 검증 경로 SHADOW 보고서','',
        'ILLUSTRATIVE — NOT LIVE DATA' if isinstance(q,dict) and q.get('mode')=='ILLUSTRATIVE' else '실제 저장 SEC 근거를 검사합니다. 독립 계약 검토가 없으면 금액은 보류합니다.',
        '검증기: '+VERIFIER,f"상태: {d['status']} / {d['calculation_status']}",
        f"금액: {d['amount'] if d['amount'] is not None else 'null (보류)'}",'', '## 보류·차단 이유','']
    lines+=['- '+code for code in d['reason_codes']]
    if a:
        r=a['result'];lines+=['',f"기업: {r['identity']['ticker']} / CIK {r['identity']['cik']}",
            f"기준시각: {r['as_of']}",f"후보 {len(r['candidates'])}; 현재 적격 계약 {len(r['verified_exposures'])}",
            '', '## 원문과 승인 경계','']
        lines += [f"- {s['accession']} / {s['form']} / SHA-256 {s['original_sha256']} / {s['source_url']}" for s in r['sources']]
    lines+=['','인증서는 입력·원문·기간·외부 owner pin의 재생 가능한 결합입니다. 자체 hash나 Boolean이 사람의 신원·독립성을 증명하지 않습니다.',
        'legacy v1 fallback 없음. 실제 의미 정확도 NOT_MEASURED. SHADOW 유지. 점수·목표가·거래 계획 null.']
    return '\n'.join(lines)+'\n'


def queue(decision):
    if decision['authorization']:return review_queue(decision['authorization']['result'])
    return {'contract':'sec-current-rejected-review-queue-v1','status':'BLOCKED','reason_codes':decision['reason_codes'],
            'actual_human_reviews_completed':0,'draft_manifests':[],'no_model_input':True}


def run(request,output_root,*,owner=None,created_at=None):
    if not Path(output_root).resolve().is_relative_to((ROOT/'reports/private').resolve()):raise Rejected('PRIVATE_CURRENT_REPORT_REQUIRED')
    at=created_at or now();d=facade.process(request,owner=owner,created_at=at)
    illustrative=isinstance(request,dict) and request.get('mode')=='ILLUSTRATIVE'
    binding=context(owner,illustrative).binding(illustrative)
    value=seal({'contract':'sec-current-shadow-bundle-v1','verification_version':VERIFIER,'facade_methods':methods(),
        'request':request,'decision':d,'owner_binding':binding,'created_at':at})
    folder=Path(output_root)/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=False)
    write_once(folder/'CURRENT.json',value);write_once(folder/'REVIEW_QUEUE.private.json',queue(d))
    with (folder/'REPORT.ko.md').open('x',encoding='utf-8') as stream:stream.write(render(value))
    write_once(folder/'FILES.json',{p.name:digest(p) for p in folder.iterdir()})
    return folder


def validate(folder,*,owner=None):
    folder=Path(folder)
    if not (folder/'CURRENT.json').is_file():raise Rejected('LEGACY_REPORT_NOT_CURRENT_APPROVAL')
    files=read(folder/'FILES.json');expected={'CURRENT.json','REVIEW_QUEUE.private.json','REPORT.ko.md'}
    if set(files)!=expected or {p.name for p in folder.iterdir()}!=expected|{'FILES.json'} or any(digest(folder/n)!=h for n,h in files.items()):raise Rejected('CURRENT_REPORT_FILES_CHANGED')
    b=read(folder/'CURRENT.json');verify_seal(b)
    if b['contract']!='sec-current-shadow-bundle-v1' or b['verification_version']!=VERIFIER or b['facade_methods']!=methods():raise Rejected('CURRENT_REPORT_METHOD_CHANGED')
    illustrative=b['request'].get('mode')=='ILLUSTRATIVE' if isinstance(b['request'],dict) else False
    if owner is None and b['owner_binding']['policy_sha256'] is not None:raise Rejected('EXTERNAL_OWNER_CONTEXT_REQUIRED')
    if context(owner,illustrative).binding(illustrative)!=b['owner_binding']:raise Rejected('EXTERNAL_OWNER_PIN_MISMATCH')
    if facade.process(b['request'],owner=owner,created_at=b['created_at'])!=b['decision']:raise Rejected('CURRENT_REPORT_REPLAY_CHANGED')
    if render(b)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8') or queue(b['decision'])!=read(folder/'REVIEW_QUEUE.private.json'):
        raise Rejected('CURRENT_REPORT_RENDER_CHANGED')
    return {'status':'VALID','verification_version':VERIFIER,'decision_status':b['decision']['status'],
            'amount':b['decision']['amount'],'new_model_calls':0}
