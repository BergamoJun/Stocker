"""Compose issuer research, source/fact criticism and metadata-only news locally."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid
import jsonschema
from research.local_files import atomic_json, exclusive_lock
from research.project_compatibility import check as historical_check
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest, stable_hash
from research.news_connection_report import text, link
from . import issuer_impact as impact, impact_critic as critic, impact_quality, analysis_contract, text_store, ondemand_news, scope, semantic_research

CHANNELS={'financing_cost':'자금조달 비용','cash_yield':'현금 운용 수익','investment_budget':'투자 지출의 조달 여건','energy_input_cost':'에너지 구매 비용','energy_sales_price':'에너지 판매 가격','supply_availability':'에너지 조달 가능성','energy_sales_demand':'에너지 판매 수요','none':'지원되는 경로 없음'}
ASSUMPTIONS={'financing_cost':'총부채 중 이자부·금리민감 부채의 실제 규모와 만기','cash_yield':'현금의 이자 지급 조건과 운용 구조','investment_budget':'추가 투자에 필요한 차입·자금조달 연결','energy_input_cost':'에너지 구매 규모·가격 연동·헤지','energy_sales_price':'에너지 판매 노출·계약 가격·헤지','supply_availability':'해당 공급량과 기업의 조달 경로 연결','energy_sales_demand':'해당 에너지 수요와 기업 매출의 연결','none':'관련 기업 노출 자료'}


def binding(path):
    p=Path(path).resolve(); return {'path':str(p),'sha256':digest(p)}


def check_binding(value):
    p=Path(value['path']).resolve()
    if not p.is_relative_to((ROOT/'reports/private').resolve()) or digest(p)!=value['sha256']: raise ValueError('IMPACT_ORIGIN_CHANGED')
    return p


def context(input_folder,news_folder,at,days):
    ai=analysis_contract.export_ai(input_folder,clock=lambda:at)
    news=ondemand_news.validate(news_folder)
    if (news['identity']['cik'],news['identity']['symbol'],news['is_illustrative'])!=(ai['identity']['cik'],ai['identity']['symbol'],ai['is_illustrative']): raise ValueError('IMPACT_NEWS_ISSUER_MISMATCH')
    if as_utc(news['generated_at'])>=as_utc(at).replace(microsecond=0) or news['lookback_days']!=days: raise ValueError('IMPACT_NEWS_TIME_OR_WINDOW_MISMATCH')
    current=text_store.packet(at,clock=lambda:at,illustrative=ai['is_illustrative'])
    rows,coverage=impact.prepare(ai,at,days,current_documents=current['documents'])
    return ai,news,rows,coverage


def assess(row,backend,gate):
    result={**row,'candidate_routes':impact.candidates(row['input']),'proposal':None,'research_usage':None,'critic':None,'status':'quality_withheld','reason_code':'MODEL_QUALITY_NOT_CLEARED'}
    if not gate: return result
    if backend is None: result.update(status='inference_deferred',reason_code='LOCAL_BACKEND_NOT_AVAILABLE'); return result
    try:
        proposal,usage=impact.research(row['input'],backend)
        result.update(proposal=proposal,research_usage=usage)
        review=critic.review(row['input'],proposal,backend)
        result.update(critic=review,status=review['status'],reason_code=None if review['status']=='conditional_context_only' else 'CRITIC_OR_SOURCE_GUARD_WITHHELD')
    except impact.local_inference.Deferred:
        result.update(status='inference_deferred',reason_code='LOCAL_INFERENCE_DEFERRED')
    except (ValueError,KeyError,jsonschema.ValidationError):
        result.update(status='analysis_rejected',reason_code='MODEL_OUTPUT_OR_SOURCE_GUARD_REJECTED')
    return result


def news_view(news,at,rows):
    # The metadata never crosses into researcher or critic prompts.
    return {'status':news['status'],'snapshot_generated_at':news['generated_at'],'window':news['window'],'snapshot_age_seconds':max(0,(as_utc(at)-as_utc(news['generated_at'])).total_seconds()),'topics':news['topics'],'article_count':news['article_count'],'articles':news['articles'],'matching_official_urls':sorted({r['source_url'] for r in rows}&{a['url'] for a in news['articles']}),'publisher_body_analyzed':False,'numeric_news_impact':None}


def root_for(illustrative):
    expected=ROOT/'reports/private/issuer_impact'/('illustrative' if illustrative else 'real')
    if expected.resolve()!=expected: raise ValueError('IMPACT_STORE_ALIAS_FORBIDDEN')
    return expected


def save(input_folder,news_folder,baseline,quality_path,*,backend=None,days=7,clock=now):
    proof=scope.check(baseline); impact.policy(clock)
    quality=impact.read(quality_path); gate=impact_quality.validate(quality)
    semantic_research.quality_gate(ROOT/'reports/private/autonomous_case/20261109/LOCAL_QUALITY.final.json')
    at=clock()
    if as_utc(quality['evaluated_at'])>=as_utc(at).replace(microsecond=0): raise ValueError('IMPACT_QUALITY_NOT_BEFORE_CUTOFF')
    ai,news,rows,coverage=context(input_folder,news_folder,at,days)
    root=root_for(ai['is_illustrative']); root.mkdir(parents=True,exist_ok=True)
    with exclusive_lock(root/'writer.lock'):
        files=[p for p in root.rglob('*') if p.is_file()]
        if len(list(root.glob('*/IMPACT.json')))>=1000 or sum(p.stat().st_size for p in files)>=134217728: raise ValueError('IMPACT_REPORT_STORAGE_CAP')
        cards=[assess(r,backend,gate) for r in rows]
        generated=clock(); ident=uuid.uuid4().hex
        value={'contract':'issuer-impact-report-v1','run_id':ident,'identity':ai['identity'],'is_illustrative':ai['is_illustrative'],'generated_at':generated,'evidence_as_of':at,'original_common_input_as_of':ai['as_of'],'lookback_days':days,'baseline_path':str(Path(baseline).resolve()),'baseline_sha256':proof['baseline_sha256'],'method_hashes':impact.methods(),'input_origin':binding(Path(input_folder)/'AI_INPUT.json'),'news_origin':binding(Path(news_folder)/'PACKET.json'),'quality_origin':binding(quality_path),'quality_decision':quality['decision'],'ai_input_sha256':stable_hash(ai),'coverage':coverage,'cards':cards,'news':news_view(news,at,rows),'external_inference_calls':0,'model_training_performed':False,'released_score':None,'released_price_plan':None,'price_effect_validated':False,'critic_statistically_independent':False}
        value['record_sha256']=stable_hash(value)
        encoded=json.dumps(value,ensure_ascii=False).encode('utf-8')
        if len(encoded)>1048576: raise ValueError('IMPACT_SINGLE_REPORT_CAP')
        folder=root/ident; folder.mkdir(exist_ok=False)
        atomic_json(folder/'IMPACT.json',value); (folder/'REPORT.ko.md').write_text(render(value),encoding='utf-8')
        atomic_json(folder/'FILES.json',{n:digest(folder/n) for n in ('IMPACT.json','REPORT.ko.md')})
    scope.check(baseline,expected_sha256=proof['baseline_sha256'])
    return value,folder


def render(v):
    accepted=sum(c['status']=='conditional_context_only' for c in v['cards'])
    lines=[f"# {text(v['identity']['symbol'])} 기업별 조건부 영향 검토",'',f"생성: {v['generated_at']} · 근거 선택 기준: {v['evidence_as_of']} · 재무/원문 묶음: {v['original_common_input_as_of']}",f"로컬 AI 품질 상태: {v['quality_decision']}",'',f"선택된 재무 항목 {v['coverage']['selected_facts']}개, 공식 사건 묶음 {len(v['cards'])}개 중 조건부 검사 통과 {accepted}개입니다. 확정 투자 점수·목표가·기간은 보류합니다.",'','이 모듈은 허용 공식 문단과 SEC 숫자에서 전달 경로를 제한적으로 선택합니다. 기업별 실제 사업 노출과 5거래일 주가 영향은 검증되지 않았습니다. 반론은 같은 로컬 모델의 별도 문맥에서 생성하므로 독립 모델의 확인이 아닙니다.','']
    if not v['cards']: lines += ['조회 기간에 맞는 판독 근거가 없습니다. 자료 부족을 중립 점수로 채우지 않습니다.','']
    for index,card in enumerate(v['cards'],1):
        x=card['input']; s=x['source']; c=x['classification']; p=card['proposal']
        lines += [f"## 공식 원문 {index}: {card['status']}",'',f"{link(card['source_url'],'공식 보완 근거')} · {s['published_date']} · 발표 시각 정밀도 {s['time_precision']}",f"원문 분류: {c['event_type']} / {c['variable_direction']} / {c['claim_kind']} · 원문 전망 기간: {c['horizon']}",'> '+text(c['quote']),'']
        if p:
            route=p['route_id']; fact=next((f for f in x['facts'] if f['fact_id']==p['fact_id']),None)
            lines += [f"분석 후보: {CHANNELS[route]} · 가정 성립 시 효과 {p['effect_if_assumption_holds']}.",f"확인되지 않은 핵심 가정: {ASSUMPTIONS[route]}."]
            if fact: lines += [f"수치 근거: {fact['metric']} / {text(fact['value'])} {fact['unit']} / {fact['basis']} / {fact['start']}~{fact['end']} / {fact['scope']} · 사실 ID {text(fact['fact_id'])}"]
        if card['critic']: lines += [f"별도 검사: {card['critic']['raw']['verdict']} / {card['critic']['raw']['reason']} · 자동 오류 {len(card['critic']['deterministic_errors'])}개."]
        if not p:
            lines += ['규칙으로 구성한 검토 후보이며 AI 채택 결과는 아닙니다:']
            for route in card['candidate_routes']: lines += [f"- {CHANNELS[route['route_id']]}: 가정 성립 시 {route['effect_if_assumption_holds']} · 확인할 사항: {ASSUMPTIONS[route['route_id']]}."]
            for fact in x['facts']:
                refs=card.get('fact_sources',{}).get(fact['fact_id'],[])
                lines += [f"- SEC 수치 맥락 {fact['metric']}: {text(fact['value'])} {fact['unit']} / {fact['basis']} / {fact['start']}~{fact['end']} / {fact['scope']} · "+' / '.join(link(ref['url'],'공시 원문') for ref in refs)]
        if card['status']!='conditional_context_only': lines += ['이 후보는 해석 근거로 채택하지 않았습니다. 사유: '+str(card['reason_code'])]
        lines += ['반대 가능성: 이미 가격에 반영됐거나 계약·헤지·사업 구조가 전달 효과를 약화시킬 수 있습니다. 확인할 사항은 위 사업 노출 가정과 후속 공시입니다.','']
    n=v['news']; lines += ['## 요청형 뉴스 링크','',f"저장 뉴스 기준: {n['snapshot_generated_at']} · 현재 보고서와 시차 {n['snapshot_age_seconds']:.0f}초 · 상태 {n['status']} · 링크 {n['article_count']}개.",'기사 본문은 이 분석기에 입력하지 않았습니다. 아래 링크와 위 공식 보완 근거는 별도 범위이며 전체 뉴스 분석 완료를 뜻하지 않습니다.','']
    for t in n['topics']: lines += [f"- {t['topic']}: {t['status']} · 미검색 구간 {t['unsearched_tail_seconds']}초"]
    lines += ['']
    for a in n['articles']: lines += ['- '+link(a['url'],text(a['title']))]
    lines += ['','재무 누락: '+(', '.join(v['coverage']['financial_gaps']) or '선택 대상 내 누락 없음'),'품질 검사는 자체 작성 개발 사례의 규칙 준수 평가입니다. 주가 예측 정확도·실제 수익성 평가와 구분합니다.','']
    return '\n'.join(lines)


def validate(folder):
    folder=Path(folder).resolve(); v=impact.read(folder/'IMPACT.json')
    if folder.parent!=root_for(v['is_illustrative']) or folder.name!=v['run_id'] or v['method_hashes']!=impact.methods() or stable_hash({k:x for k,x in v.items() if k!='record_sha256'})!=v['record_sha256']: raise ValueError('IMPACT_REPORT_CHANGED')
    historical_check(v['baseline_path'],expected_sha256=v['baseline_sha256'])
    origin=check_binding(v['input_origin']); newsfile=check_binding(v['news_origin']); qualityfile=check_binding(v['quality_origin'])
    quality=impact.read(qualityfile); gate=impact_quality.validate(quality)
    ai,news,rows,coverage=context(origin.parent,newsfile.parent,v['evidence_as_of'],v['lookback_days'])
    if v['identity']!=ai['identity'] or v['is_illustrative'] is not ai['is_illustrative'] or stable_hash(ai)!=v['ai_input_sha256'] or v['original_common_input_as_of']!=ai['as_of'] or v['coverage']!=coverage or v['quality_decision']!=quality['decision'] or len(rows)!=len(v['cards']): raise ValueError('IMPACT_CONTEXT_REPLAY_CHANGED')
    if as_utc(quality['evaluated_at'])>=as_utc(v['evidence_as_of']).replace(microsecond=0) or as_utc(v['generated_at'])<as_utc(v['evidence_as_of']): raise ValueError('IMPACT_REPORT_TIME_CHANGED')
    for row,card in zip(rows,v['cards']):
        if any(card[k]!=x for k,x in row.items()): raise ValueError('IMPACT_CARD_INPUT_CHANGED')
        if card['candidate_routes']!=impact.candidates(row['input']): raise ValueError('IMPACT_CANDIDATES_CHANGED')
        if not gate and card!=assess(row,None,False): raise ValueError('IMPACT_UNGATED_ANALYSIS')
        if card['critic']:
            if not gate or card['proposal'] is None: raise ValueError('IMPACT_UNGATED_ANALYSIS')
            rebuilt=critic.finish(card['input'],card['proposal'],card['critic']['raw'],card['critic']['usage'])
            if rebuilt!=card['critic'] or card['status']!=rebuilt['status']: raise ValueError('IMPACT_CRITIC_REPLAY_CHANGED')
        elif card!=assess(row,None,gate):
            reasons={'analysis_rejected':'MODEL_OUTPUT_OR_SOURCE_GUARD_REJECTED','inference_deferred':'LOCAL_INFERENCE_DEFERRED'}
            if not gate or card['status'] not in reasons or card['reason_code']!=reasons[card['status']] or card['proposal'] is not None and critic.errors(card['input'],card['proposal']): raise ValueError('IMPACT_WITHHOLDING_CHANGED')
    if v['news']!=news_view(news,v['evidence_as_of'],rows) or any(v[k] is not None for k in ('released_score','released_price_plan')) or any(v[k] for k in ('external_inference_calls','model_training_performed','price_effect_validated','critic_statistically_independent')): raise ValueError('IMPACT_OUTPUT_BOUNDARY_CHANGED')
    files=impact.read(folder/'FILES.json')
    if set(files)!={'IMPACT.json','REPORT.ko.md'} or any(digest(folder/n)!=sha for n,sha in files.items()) or render(v)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8'): raise ValueError('IMPACT_FILES_CHANGED')
    return v


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--ticker'); p.add_argument('--baseline',type=Path); p.add_argument('--quality',type=Path); p.add_argument('--news',type=Path); p.add_argument('--days',type=int,default=7); p.add_argument('--offline-news',action='store_true'); p.add_argument('--validate',type=Path); a=p.parse_args(argv)
    try:
        if a.validate:
            v=validate(a.validate); print(json.dumps({'status':'issuer_impact_valid','cards':len(v['cards'])})); return 0
        if not a.ticker or not a.baseline or not a.quality: p.error('--ticker, --baseline and --quality required')
        from . import weighted_engine,ondemand_transport
        from research.learned_research import latest_training_snapshot
        scope.check(a.baseline); quality=impact.read(a.quality); gate=impact_quality.validate(quality)
        news_folder=a.news
        if news_folder is None:
            _,ent=latest_training_snapshot(); client=ondemand_transport.Client(impact.read(ent),os.environ.get('MASSIVE_API_KEY',''),a.baseline)
            _,news_folder=ondemand_news.run(a.ticker.upper(),a.days,a.baseline,client=client,offline=a.offline_news)
        # Old snapshots stay usable but their original times and gaps are shown.
        input_folder=weighted_engine.resolve(a.ticker.upper())
        if gate:
            with impact.local_inference.Server(settings=impact.runtime_settings()) as backend:
                v,folder=save(input_folder,news_folder,a.baseline,a.quality,backend=backend,days=a.days)
        else: v,folder=save(input_folder,news_folder,a.baseline,a.quality,days=a.days)
        validate(folder); print(json.dumps({'status':'issuer_impact_saved','symbol':v['identity']['symbol'],'cards':len(v['cards']),'conditional_cards':sum(c['status']=='conditional_context_only' for c in v['cards']),'quality':v['quality_decision'],'folder':str(folder)})); return 0
    except Exception:
        print(json.dumps({'status':'issuer_impact_deferred','reason_code':'INPUT_POLICY_MODEL_OR_SOURCE_VALIDATION_FAILED'})); return 1

if __name__=='__main__': raise SystemExit(main())
