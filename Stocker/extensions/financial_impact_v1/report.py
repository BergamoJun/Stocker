"""Standalone immutable SHADOW report and safe legacy-report attachment."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import uuid
import jsonschema
from research.sec_transport import now
from research.rights import as_utc
from research.validation import stable_hash,digest
from extensions.research_v4 import seal,verify_seal,write_once,read
from . import methods,contract,calculations as calc,validator
from .models import RELEASE_FIELDS


def analyze(packet,*,created_at=None):
    created_at=created_at or now();issues=[];calculation=None
    # Input snapshots, including rejected JSON values, are never normalized in place.
    packet=deepcopy(packet)
    try:
        contract.validate(packet)
    except jsonschema.ValidationError as error:
        issues=[{'code':'INPUT_CONTRACT_INVALID','status':'CALCULATION_FAILED','detail':'/'.join(map(str,error.absolute_path)) or 'root'}]
    if not issues:
        issues,bound=validator.check(packet)
        if as_utc(created_at)<as_utc(packet['context']['as_of']):
            issues.append({'code':'REPORT_BEFORE_AS_OF','status':'INSUFFICIENT_EVIDENCE','detail':'created_at is earlier than as_of'})
        if not issues:
            try:
                ctx=packet['context'];event=packet['event'];values={k:f['value'] for k,f in bound.items()}
                end=ctx['calculation_end']
                start=min(end,max(ctx['calculation_start'],event['effective_date'],values['reset_date']))
                calculation=calc.interest(values['principal'],bound['principal']['unit'],values['repricing_fraction'],
                    values['hedged_fraction'],packet['assumptions']['rate_delta_override'] or event['delta'],
                    values['rate_mapping'],start,end,packet['policy']['day_count'])
                calculation['scenario_fingerprint']=validator.scenario_fingerprint(packet)
                calculation['basis_fact_ids']={k:f['fact_id'] for k,f in bound.items()}
                calculation['ledger_record']=validator.ledger_record(packet)
            except (ValueError,ArithmeticError,KeyError,TypeError):
                issues.append({'code':'CALCULATION_EXCEPTION','status':'CALCULATION_FAILED','detail':'no partial amount released'})
                calculation=None
    valid=not issues;metadata=packet if isinstance(packet,dict) and not any(i['code']=='INPUT_CONTRACT_INVALID' for i in issues) else {}
    c=metadata.get('context',{});event=metadata.get('event',{});a=metadata.get('assumptions',{})
    is_real=metadata.get('mode')=='REAL'
    value={'contract':'financial-impact-output-v1','created_at':created_at,'input_sha256':stable_hash(packet),
        'methods':methods(),'mode':'SHADOW','status':validator.primary_status(issues),
        'evidence_status':('EVIDENCE_VERIFIED' if valid else 'INSUFFICIENT_EVIDENCE') if is_real else 'ILLUSTRATIVE',
        'calculation_basis':'SCENARIO_ONLY' if not is_real or a.get('rate_delta_override') is not None else 'CONDITIONAL_EVIDENCE_INPUTS',
        'issuer':{'cik':c['cik'],'ticker':c['ticker']} if 'cik' in c and 'ticker' in c else None,
        'event_id':event.get('event_id'),'scenario_id':a.get('scenario_id'),
        'amount':calculation['amount'] if calculation else None,'currency':c.get('currency'),
        'metric':'interest_expense_delta_pre_tax','calculation':calculation,'issues':issues,
        'conditions':a.get('valid_if',[])+['Contractual principal, mapping, reset, hedge coverage and accounting treatment remain as supplied.'],
        'invalidations':a.get('invalid_if',[])+['Amortization, cap/floor binding, basis changes, hedge changes or conflicting later filings invalidate this linear calculation.'],
        'countereffects':['Cash yields, refinancing decisions, revenues and taxes may offset or amplify the expense change; not netted without separate verified evidence.'],
        'semantic_market_accuracy':'NOT_MEASURED','release':{k:None for k in RELEASE_FIELDS}}
    value=seal(value)
    contract.validate(value,'output')
    return value


def replay(packet,value):
    contract.validate(value,'output');verify_seal(value)
    if analyze(packet,created_at=value['created_at'])!=value:raise ValueError('IMPACT_REPLAY_CHANGED')
    return {'status':'VALID','new_model_calls':0}


def legacy_reference(folder,packet):
    """Read-only composition: the pinned legacy file/contract is never modified."""
    from extensions import research_v3_report_v2,research_v3_report
    folder=Path(folder)
    if (folder/'RESEARCH_V3_1.json').is_file():value=research_v3_report_v2.validate(folder);kind='issuer-research-report-v3.1'
    elif (folder/'RESEARCH_V3.json').is_file():value=research_v3_report.validate(folder);kind='issuer-research-report-v3'
    else:raise ValueError('UNSUPPORTED_LEGACY_REPORT')
    if value['identity']['cik']!=packet['context']['cik'] or value['identity']['symbol']!=packet['context']['ticker'] or value['evidence_as_of']!=packet['context']['as_of']:
        raise ValueError('LEGACY_IDENTITY_OR_CUTOFF_MISMATCH')
    return {'folder':str(folder.resolve()),'contract':kind,'manifest_sha256':digest(folder/'FILES.json')}


def render(bundle):
    packet,value=bundle['input'],bundle['output'];c=packet['context'];a=packet['assumptions']
    lines=['# ImpactEngine v1 SHADOW 재무 영향 분석','',
        'ILLUSTRATIVE — 가상 입력입니다. 실제 기업의 확인된 영향이 아닙니다.' if packet['mode']=='ILLUSTRATIVE' else '실제 저장 자료의 근거 충족 여부를 검사한 조건부 분석입니다.',
        '',f"기업: {c['ticker']} / CIK {c['cik']}",f"기준시각: {c['as_of']}",
        f"사건: {packet['event']['event_id']} / {packet['event']['event_type']}",
        f"회계 범위: {c['accounting_scope']} / 보고 기간: {json.dumps(c['reporting_period'],ensure_ascii=False)}",
        f"계산 기간: [{c['calculation_start']}, {c['calculation_end']})",f"시나리오: {a['label']} / {a['description']}",
        '',f"계산 상태: {value['status']}",f"근거 상태: {value['evidence_status']}",
        f"조건부 이자비용 증감: {value['amount'] if value['amount'] is not None else 'null (계산 보류)'} {value['currency']}",
        '양수는 세전 이자비용 증가, 음수는 감소입니다. 주가 방향·순이익·실제 손익 입증이 아닙니다.',
        '', '## 재무 입력과 산식','']
    if value['calculation']:lines+=['```json',json.dumps(value['calculation'],ensure_ascii=False,indent=2),'```']
    else:lines+=['계약으로 확인된 재설정 원금·금리 연동·기간·헤지·회계 처리가 모두 있어야 계산합니다. 총부채/총매출은 대체 입력이 아닙니다.']
    lines+=['','## 실제 저장 재무 맥락 (계산 투입 근거와 구분)','',
        '```json',json.dumps(packet['context_facts'],ensure_ascii=False,indent=2),'```',
        '', '## 부족하거나 충돌하는 근거','']
    lines += [f"- {i['code']}: {i['detail']}" for i in value['issues']] or ['없음 (입력의 가정·근거 계약 범위 내 산술 검증)']
    lines+=['','## Base / Bull / Bear (서로 대안인 재무 시나리오)','',
        '| 시나리오 | 가정 금리 변화 | 이자비용 증감 | 상태 |','|---|---|---|---|']
    for row in bundle['scenarios']:
        p,r=row['input'],row['output'];shock=p['assumptions']['rate_delta_override']
        lines.append(f"| {p['assumptions']['label']} | {shock['value']} {shock['unit']} | {r['amount'] if r['amount'] is not None else 'null'} | {r['status']} |")
    lines+=['','Bull/Bear는 기업 이자비용의 유불리이며 주가 시나리오가 아닙니다. 확률을 부여하지 않고 시나리오끼리 합산하지 않습니다.',
        '', '## 유효 조건 / 무효화 조건','']+[f'- {s}' for s in value['conditions']+value['invalidations']]
    lines+=['','## 반대 효과와 미확인 정보','']+[f'- {s}' for s in value['countereffects']+packet['event']['uncertainties']]
    lines+=['','## 원문과 위치','']
    for s in packet['sources']:lines.append(f"- {s['source_id']}: {s['url']} / SHA-256 {s['original_sha256']} / 공개 {s['published_at']} / 가용 {s['available_at']}")
    for f in packet['facts']:lines.append(f"- {f['fact_id']} / {f['kind']} / {f['source_id']} [{f['start_offset']}, {f['end_offset']}) / {f['verification_status']} / {f['exact_quote']}")
    lines+=['','주가 수익 예측·목표가·매력도·매수/매도가·배분은 모두 null입니다. 실제 시장 의미 정확도 NOT_MEASURED.']
    return '\n'.join(lines)+'\n'


def write(packet,output_root,*,include_scenarios=True,legacy=None,created_at=None):
    from .scenarios import standard
    at=created_at or now();value=analyze(packet,created_at=at)
    bundle={'contract':'financial-impact-shadow-bundle-v1','input':deepcopy(packet),'output':value,
        'scenarios':standard(packet,created_at=at) if include_scenarios else [],
        'legacy_reference':legacy_reference(legacy,packet) if legacy else None}
    folder=Path(output_root)/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=False)
    write_once(folder/'IMPACT.json',bundle)
    with (folder/'REPORT.ko.md').open('x',encoding='utf-8') as stream:stream.write(render(bundle))
    write_once(folder/'FILES.json',{p.name:digest(p) for p in folder.iterdir()})
    validate(folder);return folder


def validate(folder):
    folder=Path(folder);files=read(folder/'FILES.json')
    if set(files)!={'IMPACT.json','REPORT.ko.md'} or {p.name for p in folder.iterdir()}!=set(files)|{'FILES.json'} or any(digest(folder/n)!=h for n,h in files.items()):
        raise ValueError('IMPACT_REPORT_FILES_CHANGED')
    bundle=read(folder/'IMPACT.json')
    if set(bundle)!={'contract','input','output','scenarios','legacy_reference'} or bundle['contract']!='financial-impact-shadow-bundle-v1':raise ValueError('IMPACT_BUNDLE_CONTRACT_CHANGED')
    replay(bundle['input'],bundle['output'])
    from .scenarios import standard
    if bundle['scenarios'] and standard(bundle['input'],created_at=bundle['output']['created_at'])!=bundle['scenarios']:
        raise ValueError('IMPACT_SCENARIOS_CHANGED')
    if bundle['legacy_reference'] and legacy_reference(bundle['legacy_reference']['folder'],bundle['input'])!=bundle['legacy_reference']:
        raise ValueError('IMPACT_LEGACY_REFERENCE_CHANGED')
    if (folder/'REPORT.ko.md').read_text(encoding='utf-8')!=render(bundle):raise ValueError('IMPACT_RENDER_CHANGED')
    return {'status':'VALID','mode':'SHADOW','calculation_status':bundle['output']['status']}
