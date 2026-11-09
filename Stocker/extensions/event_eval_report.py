"""Source-event corpus and independent evaluation status, never fabricated labels."""
import json,uuid
from pathlib import Path
from datetime import timedelta
import jsonschema
from research.local_files import atomic_json
from research.project_compatibility import check as historical_check
from research.sec_report import ROOT
from research.sec_transport import now
from research.rights import as_utc
from research.validation import digest,stable_hash
from . import event_evaluation as events,event_outcomes,analysis_contract,exposure_report,scope


def case(ai,exposure,at,kind,url,raw_hash,source_available,published_date,published_at,retrieved_at,source_id):
    identity=ai['identity']; group=stable_hash({'source_url':url,'published_date':published_date})
    ident=stable_hash({'group':group,'cik':identity['cik']})
    safe_exposure=[{k:r[k] for k in ('id','kind','status','fact_ids','assumptions')} for r in exposure['exposure']['items']]
    return {'case_id':ident,'event_group_id':group,'identity':identity,'event_kind':kind,'source_url':url,'source_record_sha256':raw_hash,'source_id':source_id,'source_published_date':published_date,'source_provenance_verified':True,'first_public_precision':'date','first_public_verified':False,'times':{'event_at':None,'first_public_at':None,'source_published_at':published_at,'retrieved_at':retrieved_at,'analysis_cutoff':at,'first_local_available_at':source_available},'exposure_as_of':exposure['exposure']['as_of'],'exposure_first_available_at':exposure['generated_at'],'exposure_input_sha256':exposure['exposure']['input_sha256'],'exposure_coverage_complete':False,'exposure_view':safe_exposure,'ai_input':ai,'is_illustrative':ai['is_illustrative'],'prediction_frozen_at':None,'partition':'unassigned','overlapping_events':None,'overlap_reviewed':False,'context_vintage':'assembled_now_not_available_at_historical_publication','independent_gold_status':'pending','original_event_time_verified':False}


def corpus(ais,exposures,at):
    cases=[]
    for ai,x in zip(ais,exposures):
        if ai['identity']!=x['exposure']['identity'] or stable_hash(ai)!=x['exposure']['input_sha256']: raise ValueError('EVENT_EXPOSURE_ISSUER_OR_SOURCE_MISMATCH')
        facts=ai['financial_facts']; latest=max(f['end'] for f in facts)
        f=sorted((f for f in facts if f['end']==latest),key=lambda f:f['fact_id'])[0]; ref=f['source_refs'][0]
        cases.append(case(ai,x,at,'reviewed_sec_filing_update',ref['url'],ref['raw_sha256'],f['available_at'],None,None,None,ref['source_id']))
        docs=sorted(ai['official_documents'],key=lambda d:(d['published_date'],d['source_id']),reverse=True)
        if docs:
            d=docs[0]
            cases.append(case(ai,x,at,'shared_official_document_publication',d['url'],d['record_sha256'],d['available_at'],d['published_date'],d['published_at'],None,d['source_id']))
    if len({c['case_id'] for c in cases})!=len(cases): raise ValueError('DUPLICATE_CORPUS_SOURCE_EVENT')
    return cases


def evaluate(cases,observed_at):
    for c in cases: events.model_view(c)
    prices=[event_outcomes.evaluate(c,observed_at) for c in cases]
    return {'interpretation':events.interpretation(cases,[],[]),'price_evaluation':prices,'price_evaluated_cases':sum(r['outcome'] is not None for r in prices),'actual_model_calls':0,'independent_gold_cases':0,'price_model_changed':False,'coefficients_changed':False,'locked_final_read':False,'released_score':None,'released_price_plan':None,'status':'evidence_corpus_ready_validation_not_run'}


def render(v):
    r=v['evaluation']; a=r['interpretation']
    lines=['# Priority 3: 사건 해석과 가격 예측의 독립 평가','',f"사례 {a['total_cases']}행 · 서로 다른 원천 사건 키 {a['distinct_event_groups']}개 · 독립 정답 {a['qualified_gold_cases']}개 · 실제 모델 평가 {a['evaluated_cases']}개 · 가격 평가 {r['price_evaluated_cases']}개.",'','한 공식 문서를 여러 기업에 연결한 행은 공통 사건 그룹으로 묶습니다. 원천 사건 키가 다르다는 사실만으로 통계적 독립성을 인증하지 않습니다. 기업 공시 자료와 공식 문서의 새 평가 후보를 만들었으며 정답이나 최초 공개 시각을 지어내지 않았습니다.','', '| 기업 | 후보 사건 | 가격 평가 보류 사유 |','|---|---|---|']
    for c,p in zip(v['cases'],r['price_evaluation']): lines.append(f"| {c['identity']['symbol']} | {c['event_kind']} | {', '.join(p['eligibility']['reasons'])} |")
    lines+=['','독립 평가 정답이 없어 사건 해석 정확도는 미실행입니다. 현재 기업 노출은 사후에 조립한 검토 자료이며 과거 공개 당시의 입력으로 소급하지 않습니다. 최초 공개·사건 시각, 독립 수집 시각 및 사전 예측 기록이 부족해 가격 평가도 보류합니다. 미실행을 0% 정확도나 중립 효과로 바꾸지 않습니다.','','코드는 미국 정규장·휴일·단축장·서머타임을 고려해 발표 뒤 시작하는 완전한 정규장 5개를 선택합니다. 장 마감 후 발표를 같은 날 정규장 수익에 붙이지 않고, 장중 발표의 당일 전체 등락을 순수 발표 후 효과라고 표시하지 않습니다. 거래대금 근사는 종가×보고 거래량이며 기관 자금 유입이 아닙니다.','','가격은 별도 로컬 입력·권리 검사 뒤에만 읽으며 모델 입력이나 정답에 섞지 않습니다. 시장/업종 차감 등락은 관측적 비교이고 인과효과·전략 수익·예측력 개선 인증이 아닙니다. 기업행동·거래정지·단위 조정·동일 세션·20개 사전 활동 표본을 확인하지 못하면 보류합니다.','','실제 성능 평가는 독립적인 정답 검토, 정확한 최초 공개·실제 보관 시각, 당시 이용 가능한 노출·사전 고정 예측과 관측 완료 가격이 확보된 뒤 새 불변 평가로 실행해야 합니다. 현재 뉴스 가점·목표가·추천도는 도입하지 않습니다.','']
    return '\n'.join(lines)


def save(origins,exposure_folders,baseline,*,clock=now):
    if len(origins)!=len(exposure_folders): raise ValueError('EVENT_ORIGIN_COUNT_MISMATCH')
    proof=scope.check(baseline); at=clock(); ais=[analysis_contract.export_ai(p,clock=lambda:at) for p in origins]; xs=[exposure_report.validate(p) for p in exposure_folders]
    cases=corpus(ais,xs,at); ident=uuid.uuid4().hex
    v={'contract':'event-evaluation-report-v1','run_id':ident,'created_at':at,'origins':[str(Path(p).resolve()) for p in origins],'origin_hashes':[digest(Path(p)/'AI_INPUT.json') for p in origins],'exposure_origins':[str(Path(p).resolve()) for p in exposure_folders],'exposure_hashes':[digest(Path(p)/'EXPOSURE.json') for p in exposure_folders],'baseline_path':str(Path(baseline).resolve()),'baseline_sha256':proof['baseline_sha256'],'method_hashes':events.methods(),'cases':cases,'evaluation':evaluate(cases,at),'gold_status':'not_independently_adjudicated','model_predictions':[]}
    v['record_sha256']=stable_hash(v); root=ROOT/'reports/private/event_evaluations'
    if root.resolve()!=root: raise ValueError('EVENT_STORE_ALIAS')
    folder=root/ident; folder.mkdir(parents=True,exist_ok=False); atomic_json(folder/'EVENTS.json',v)
    (folder/'REPORT.ko.md').write_text(render(v),encoding='utf-8'); atomic_json(folder/'FILES.json',{n:digest(folder/n) for n in ('EVENTS.json','REPORT.ko.md')}); return v,folder


def validate(folder):
    folder=Path(folder).resolve(); v=events.read(folder/'EVENTS.json')
    if folder.parent!=(ROOT/'reports/private/event_evaluations').resolve() or folder.name!=v['run_id'] or v['method_hashes']!=events.methods() or v['record_sha256']!=stable_hash({k:x for k,x in v.items() if k!='record_sha256'}): raise ValueError('EVENT_REPORT_CHANGED')
    historical_check(v['baseline_path'],expected_sha256=v['baseline_sha256'])
    ais=[analysis_contract.export_ai(p,clock=lambda:v['created_at']) for p in v['origins']]; xs=[exposure_report.validate(p) for p in v['exposure_origins']]
    if v['origin_hashes']!=[digest(Path(p)/'AI_INPUT.json') for p in v['origins']] or v['exposure_hashes']!=[digest(Path(p)/'EXPOSURE.json') for p in v['exposure_origins']]: raise ValueError('EVENT_ORIGIN_CHANGED')
    cases=corpus(ais,xs,v['created_at'])
    if cases!=v['cases'] or evaluate(cases,v['created_at'])!=v['evaluation'] or v['model_predictions'] or v['gold_status']!='not_independently_adjudicated': raise ValueError('EVENT_EVALUATION_REPLAY_CHANGED')
    jsonschema.validate(v,events.read(events.BASE/'event_eval.schema.json')); files=events.read(folder/'FILES.json')
    if set(files)!={'EVENTS.json','REPORT.ko.md'} or any(digest(folder/n)!=s for n,s in files.items()) or render(v)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8'): raise ValueError('EVENT_FILES_CHANGED')
    return v
