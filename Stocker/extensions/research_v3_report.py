"""Immutable issuer-report composition with a v3 shadow analysis sidecar."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import uuid

import jsonschema

from research.local_files import atomic_json
from research.project_compatibility import check as historical_check
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest, stable_hash
from research.news_connection_report import text, link
from . import research_v3 as engine, research_v3_contract as contract, analysis_contract
from . import exposure_v3, semantic_v3, scope, weighted_engine, impact_report, local_inference


def legacy_component(baseline, ai):
    found = []
    for path in contract.read(baseline).get('issuer_impact_reports', []):
        value = contract.read(Path(path) / 'IMPACT.json')
        if value['identity'] == ai['identity'] and value['is_illustrative'] == ai['is_illustrative']:
            impact_report.validate(path)
            found.append((path, value))
    if not found:
        return None
    path, value = max(found, key=lambda pair: as_utc(pair[1]['generated_at']))
    return {'folder': str(Path(path).resolve()), 'sha256': digest(Path(path) / 'IMPACT.json'),
            'render_sha256': digest(Path(path) / 'REPORT.ko.md'), 'generated_at': value['generated_at'],
            'news': value['news'], 'quality_decision': value['quality_decision']}


def build(input_folder, baseline, *, backend=None, mode='v3_shadow', configuration='A',
          clock=now, run_id=None, inference_reason=None):
    if mode not in {'v3_shadow', 'v3_candidate'}:
        raise ValueError('V3_MODE_INVALID_USE_UNCHANGED_LEGACY_CLI')
    proof = scope.check(baseline)
    generated = clock()
    ai = analysis_contract.export_ai(input_folder, clock=lambda: generated)
    contract.validate_ai(ai, generated)
    calls_before = getattr(backend, 'calls', 0)
    result = engine.analyze(ai, backend, configuration=configuration)
    settings = contract.runtime_settings(configuration)
    value = {'contract': 'issuer-research-report-v3', 'run_id': run_id or uuid.uuid4().hex,
        'identity': ai['identity'], 'is_illustrative': ai['is_illustrative'], 'started_at': generated, 'generated_at': clock(),
        'evidence_as_of': ai['as_of'], 'mode': mode, 'configuration': configuration,
        'method_hashes': contract.methods(), 'origin': {'input_folder': str(Path(input_folder).resolve()),
            'input_sha256': digest(Path(input_folder) / 'AI_INPUT.json'),
            'baseline_path': str(Path(baseline).resolve()), 'baseline_sha256': proof['baseline_sha256']},
        'legacy_component': legacy_component(baseline, ai), 'analysis': result,
        'model_execution': 'NOT_RUN' if backend is None else 'ATTEMPTED',
        'model_calls': getattr(backend, 'calls', 0) - calls_before,
        'model': {k: settings[k] for k in ('model_id', 'model_revision', 'model_sha256', 'seed', 'temperature', 'thinking_enabled')},
        'inference_kind': 'NOT_RUN' if backend is None else 'local_qwen_cpu' if isinstance(backend, local_inference.Server) else 'MOCK_NOT_ACTUAL_MODEL',
        'inference_reason': inference_reason if backend is None else None,
        'quality_status': 'NOT_EVALUATED_FOR_ADOPTION',
        'candidate_registration': {'requested': mode == 'v3_candidate', 'eligible': False,
                                    'reason': 'separate_actual_quality_receipt_and_approval_required'},
        'official_ai_adopted': False, 'critic_statistically_independent': False,
        'released_score': None, 'released_target': None, 'released_price_plan': None,
        'external_inference_calls': 0, 'training': False}
    value['record_sha256'] = stable_hash(value)
    contract.validate(value, 'report')
    return value, ai


def render(value, ai):
    a = value['analysis']
    lines = [f"# {text(value['identity']['symbol'])} 근거 기반 연구 v3", '',
        f"생성: {value['generated_at']} · 자료 기준: {value['evidence_as_of']}", '',
        '가상 예제 — 실제 시장 자료 아님' if value['is_illustrative'] else '기존에 저장하고 재생 검증한 실제 자료를 사용한 개인용 연구 보고서입니다.', '',
        f"실행 모드: {value['mode']} · 모델 실행: {value['model_execution']} · 품질 평가: {value['quality_status']}", '',
        '이 결과는 별도 시험 경로입니다. 정식 AI 채택·투자 점수·목표가·매매 계획은 보류합니다. 같은 모델의 두 역할은 통계적으로 독립된 검증이 아닙니다.', '',
        '## 1. 검증된 재무 사실', '',
        '아래 확인은 공시의 개별 숫자·기간·단위에 한정됩니다. 기업 전체의 경제적 민감도가 확인됐다는 뜻은 아닙니다.', '']
    for f in ai['financial_facts']:
        lines.append(f"- {text(f['metric'])}: {text(f['value'])} {text(f['unit'])} · {text(f['basis'])} · {f['start'] or '시점 잔액'}~{f['end']} · {text(f['scope'])}")
        lines.append('  - 근거: ' + ' / '.join(link(s['url'], 'SEC 공시') for s in f['source_refs']) + f" · 사실 ID {text(f['fact_id'])} · 최초 이용 가능 {f['available_at']}")
    if not ai['financial_facts']:
        lines.append('확인된 재무 숫자가 없습니다.')
    lines += ['', '## 2. 원문에 직접 명시된 경제 사건', '']
    docs = {d['source_id']: d for d in ai['official_documents']}
    for p in a['propositions']:
        lines += [f"- {text(p['exact_quote'])}",
                  f"  - {text(p['economic_variable'])} / {p['variable_direction']} / {p['statement_type']} · 의미 검사 {p['evidence_status']}",
                  f"  - {link(docs[p['source_id']]['url'], '공식 원문')} · 문단 {text(p['paragraph_id'])} · 발행일 {docs[p['source_id']]['published_date']} · 시각 정밀도 {docs[p['source_id']]['time_precision']}"]
    if not a['propositions']:
        lines.append('모델이 채택 가능한 명제를 생성하지 않았습니다. 원문 보관·링크 연결과 실제 AI 판독은 별도입니다.')
    lines += ['', '## 3. 제안된 조건부 전달 경로', '']
    for card in a['cards']:
        lines.append(f"- 판정: {card['final_status']} · 합산 효과: 미확정")
        for r in card['routes']['applicable_routes'] + card['routes']['hypothetical_routes']:
            lines += [f"  - {r['route_id']}: 기업 노출 {r['exposure_status']} · 경제적 효과 {r['economic_effect_direction']}",
                      f"  - 추가 조건: {text(', '.join(r['assumptions']) or '기전의 일반 가정과 순효과 검증')}"]
    if not a['cards']:
        lines.append('전달 경로의 AI 선택은 보류입니다. 일반 매출만으로 에너지 구매·판매 경로를 적용하지 않습니다.')
        for h in a['exposure']['legacy_exposure']['hypotheses']:
            lines.append(f"- 재무 맥락에서 유지한 가설 {h['id']}: HYPOTHETICAL · 조건 {text(', '.join(h['assumptions']))} · 사실 ID {text(', '.join(h['context_fact_ids']))}. 사건 연결과 기업 적용·방향은 추가 확인 전입니다.")
    for section, wanted in [('4. 확인된 기업 노출의 구성 요소', {'VERIFIED'}),
                            ('5. 확인되지 않은 기업 노출', {'INFERRED', 'UNKNOWN', 'CONFLICTED'})]:
        lines += ['', '## ' + section, '']
        for r in a['exposure']['requirements']:
            if r['status'] in wanted:
                lines.append(f"- {r['requirement_id']}: {r['status']} · 근거 사실 {text(', '.join(r['fact_ids']) or '없음')}")
    lines += ['', 'VERIFIED는 명시된 구성 요소의 확인입니다. 헤지 후 순민감도나 5거래일 주가 효과를 뜻하지 않습니다.', '',
              '## 6. 실제 반대 증거', '']
    counter = [ref for c in a['cards'] for ref in c['actual_counter_evidence']]
    for ref in counter:
        lines.append('- ' + text(ref['exact_quote']) + ' · ' + link(docs[ref['source_id']]['url'], '반대 근거 원문'))
    if not counter:
        lines.append('NOT_FOUND_OR_UNAVAILABLE — 이번 입력에서 검증된 반대 증거를 찾지 못했거나 이용할 수 없습니다. 반대 사실이 없다는 증명은 아닙니다.')
    lines += ['', '## 7. 일반적인 해석상 한계', '',
              '계약·가격 전가·헤지·상반된 사업 효과·시장 기대와 선반영을 충분히 확인하지 못했습니다. 원문의 경제 변수 기간을 기업 주가의 기간으로 옮기지 않습니다. 영향 크기와 확률·순효과는 미확정입니다.', '',
              '## 8. 미해결 충돌', '']
    conflicts = [r['requirement_id'] for r in a['exposure']['requirements'] if r['status'] == 'CONFLICTED']
    lines.append(text(', '.join(conflicts)) if conflicts else '이번 구조 검사에서 확인된 노출 충돌은 없습니다. 자료가 부족한 항목의 무충돌을 보장하지 않습니다.')
    lines += ['', '## 9. 분석 보류 사유', '',
              text(value['inference_reason'] or '실제 모델 품질과 기업 노출 근거의 별도 검증이 필요합니다.'),
              f"처리한 문단 {a['coverage']['completed_paragraphs']}/{a['coverage']['expected_paragraphs']} · 상한으로 미처리 {a['coverage']['unprocessed_paragraphs']}"]
    for c in a['cards']:
        lines.append('- ' + text(', '.join(c['withholding_reasons']) or '조건부 기전만 검토; 정식 채택 보류'))
    lines += ['', '## 10. 후속 관찰 지표', '',
              '차입 계약의 금리 재설정·만기, 현금 이자 조건, 실제 에너지 조달·판매 계약과 가격 연동·헤지, 새 공식 원문과 상충 자료를 확인합니다. 원래 시점의 자료와 이후 발표를 분리해 기록합니다.', '',
              '## 기존 보고서와 뉴스 링크', '']
    legacy = value['legacy_component']
    if legacy:
        lines += ['[원래 기업별 보고서 사본](LEGACY.ko.md)', '',
                  f"원래 생성: {legacy['generated_at']} · 당시 AI 품질: {legacy['quality_decision']}", '',
                  '아래는 기존에 확보한 뉴스 링크입니다. 언론사 본문을 이번 모델로 분석한 것으로 표시하지 않습니다. 새로 검색한 최신 뉴스가 아닙니다.', '']
        for article in legacy['news']['articles']:
            lines.append('- ' + link(article['url'], article.get('title') or '뉴스 원문'))
    else:
        lines.append('연결할 기존 영향 보고서가 없습니다. 뉴스 본문 분석·완전한 취재 범위를 주장하지 않습니다.')
    return '\n'.join(lines) + '\n'


def write(value, ai, output=None):
    contract.validate(value, 'report')
    root = Path(output or ROOT / 'reports/private/research_v3').resolve()
    if not value['is_illustrative'] and not root.is_relative_to((ROOT / 'reports/private').resolve()):
        raise ValueError('V3_PRIVATE_OUTPUT_REQUIRED')
    folder = root / ('illustrative' if value['is_illustrative'] else 'real') / value['identity']['cik'] / value['run_id']
    folder.mkdir(parents=True, exist_ok=False)
    atomic_json(folder / 'RESEARCH_V3.json', value)
    atomic_json(folder / 'SOURCE_INPUT.json', ai)
    (folder / 'REPORT.ko.md').write_text(render(value, ai), encoding='utf-8')
    if value['legacy_component']:
        source = Path(value['legacy_component']['folder']) / 'REPORT.ko.md'
        (folder / 'LEGACY.ko.md').write_bytes(source.read_bytes())
    atomic_json(folder / 'FILES.json', {p.name: digest(p) for p in folder.iterdir() if p.is_file()})
    return folder


def replay_analysis(ai, analysis):
    rebuilt = deepcopy(analysis)
    rebuilt['exposure'] = exposure_v3.derive(ai, ai['as_of'])
    expected = engine.payloads(ai)[:contract.policy()['maximum_paragraphs']]
    if [r['payload'] for r in analysis['semantic_rows']] != expected:
        raise ValueError('V3_SEMANTIC_COVERAGE_CHANGED')
    props = []
    for row in rebuilt['semantic_rows']:
        if row['status'] == 'COMPLETED':
            row['propositions'] = semantic_v3.bind(row['raw'], row['payload'])
        elif row['propositions']:
            raise ValueError('V3_FAILED_SEMANTIC_HAS_PROPOSITIONS')
        props.extend(row['propositions'])
    rebuilt['propositions'] = props
    canonical, duplicate = {}, []
    for p in props:
        key = (p['source_id'], p['economic_variable'], p['exact_quote'], p['temporal_reference'], p['condition_quote'])
        if key in canonical:
            duplicate.append({'proposition_id': p['proposition_id'], 'duplicate_of': canonical[key]['proposition_id']})
        else:
            canonical[key] = p
    rebuilt['duplicate_events'] = duplicate
    eligible = list(canonical.values())[:contract.policy()['maximum_cards']]
    if [c['proposition_id'] for c in analysis['cards']] != [p['proposition_id'] for p in eligible]:
        raise ValueError('V3_CARD_COVERAGE_CHANGED')
    rebuilt['cards'] = []
    for p, card in zip(eligible, analysis['cards']):
        for key, kind in [('researcher_raw_output', 'researcher_raw'), ('critic_raw_output', 'critic_raw')]:
            if card[key]['status'] == 'COMPLETED':
                contract.validate(card[key]['raw_output'], kind)
        payload = next(r['payload'] for r in analysis['semantic_rows'] if r['payload']['source_id'] == p['source_id'] and r['payload']['paragraph_id'] == p['paragraph_id'])
        rebuilt['cards'].append(engine.finish(ai, p, payload, exposure_v3.routes(ai, p, rebuilt['exposure']), props,
                                              card['researcher_raw_output'], card['critic_raw_output']))
    rebuilt['coverage'] = {'expected_paragraphs': len(engine.payloads(ai)), 'attempted_paragraphs': len(expected),
        'unprocessed_paragraphs': len(engine.payloads(ai)) - len(expected),
        'completed_paragraphs': sum(r['status'] == 'COMPLETED' for r in rebuilt['semantic_rows']),
        'atomic_propositions': len(props), 'event_groups': len(canonical), 'omitted_cards': max(0, len(canonical) - len(eligible))}
    if rebuilt != analysis:
        raise ValueError('V3_ANALYSIS_REPLAY_CHANGED')


def validate(folder):
    folder = Path(folder)
    value = contract.read(folder / 'RESEARCH_V3.json')
    contract.validate(value, 'report')
    if value['run_id'] != folder.name or value['method_hashes'] != contract.methods() or value['record_sha256'] != stable_hash({k: v for k, v in value.items() if k != 'record_sha256'}):
        raise ValueError('V3_RECORD_OR_METHOD_CHANGED')
    origin = value['origin']
    historical_check(origin['baseline_path'], expected_sha256=origin['baseline_sha256'])
    ai = analysis_contract.export_ai(origin['input_folder'], clock=lambda: value['generated_at'])
    if ai != contract.read(folder / 'SOURCE_INPUT.json') or digest(Path(origin['input_folder']) / 'AI_INPUT.json') != origin['input_sha256']:
        raise ValueError('V3_SOURCE_SNAPSHOT_CHANGED')
    if value['identity'] != ai['identity'] or value['is_illustrative'] != ai['is_illustrative'] or value['evidence_as_of'] != ai['as_of']:
        raise ValueError('V3_ISSUER_OR_CUTOFF_CHANGED')
    if not as_utc(ai['as_of']) <= as_utc(value['started_at']) <= as_utc(value['generated_at']):
        raise ValueError('V3_OUTPUT_TIME_INVALID')
    if value['configuration'] != value['analysis']['configuration'] or value['candidate_registration']['requested'] != (value['mode'] == 'v3_candidate'):
        raise ValueError('V3_MODE_OR_CONFIGURATION_CHANGED')
    settings = contract.runtime_settings(value['configuration'])
    if value['model'] != {k: settings[k] for k in value['model']} or value['model_execution'] == 'NOT_RUN' and (value['model_calls'] or value['inference_kind'] != 'NOT_RUN'):
        raise ValueError('V3_MODEL_CONFIGURATION_CHANGED')
    if value['legacy_component'] != legacy_component(origin['baseline_path'], ai):
        raise ValueError('V3_LEGACY_BINDING_CHANGED')
    replay_analysis(ai, value['analysis'])
    if render(value, ai) != (folder / 'REPORT.ko.md').read_text(encoding='utf-8'):
        raise ValueError('V3_RENDER_CHANGED')
    expected = {'RESEARCH_V3.json', 'SOURCE_INPUT.json', 'REPORT.ko.md'}
    if value['legacy_component']:
        expected.add('LEGACY.ko.md')
        if digest(folder / 'LEGACY.ko.md') != value['legacy_component']['render_sha256']:
            raise ValueError('V3_LEGACY_COPY_CHANGED')
    files = contract.read(folder / 'FILES.json')
    if set(files) != expected or any(digest(folder / name) != sha for name, sha in files.items()):
        raise ValueError('V3_FILE_BUNDLE_CHANGED')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker'); parser.add_argument('--input', type=Path)
    parser.add_argument('--baseline', type=Path); parser.add_argument('--validate', type=Path)
    parser.add_argument('--mode', choices=['v3_shadow', 'v3_candidate'], default='v3_shadow')
    parser.add_argument('--configuration', choices=list('ABCDE'), default='A')
    parser.add_argument('--infer', action='store_true')
    args = parser.parse_args(argv)
    if args.validate:
        v = validate(args.validate)
        print(json.dumps({'status': 'v3_report_valid', 'symbol': v['identity']['symbol']}))
        return 0
    if not args.baseline or bool(args.ticker) == bool(args.input):
        parser.error('--baseline and exactly one of --ticker / --input required')
    source = args.input or weighted_engine.resolve(args.ticker.upper())
    if args.infer:
        try:
            with local_inference.Server(settings=contract.runtime_settings(args.configuration)) as backend:
                value, ai = build(source, args.baseline, backend=backend, mode=args.mode, configuration=args.configuration)
        except local_inference.Deferred as error:
            value, ai = build(source, args.baseline, mode=args.mode, configuration=args.configuration, inference_reason=str(error))
    else:
        value, ai = build(source, args.baseline, mode=args.mode, configuration=args.configuration,
                          inference_reason='INFERENCE_NOT_REQUESTED')
    folder = write(value, ai)
    validate(folder)
    print(json.dumps({'status': 'v3_shadow_saved', 'symbol': value['identity']['symbol'],
                      'model_execution': value['model_execution'], 'folder': str(folder)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
