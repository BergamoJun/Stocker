"""Korean investment memo from canonical P4 values and separately labelled AI proposals."""
from decimal import Decimal
from html import escape
from research.validation import stable_hash
from . import evidence_pack,scenario_review,llm_adapter,grounding,synthesis

VERSION='investment-memo-v1.1'


def fmt(value,unit='USD'):
    if value is None:return '미확인'
    n=Decimal(str(value))
    return f'{n/1000000:,.2f}백만 USD' if unit=='USD' else f'{n*100:,.2f}%' if unit=='ratio' else f'{n:,.2f}%' if unit=='percent' else f'{n:,.4f} {unit}'


def compose(value):
    pack=value['evidence_pack'];review=value['scenario_review'];entries=pack['entries'];sections=[]
    def add(title,text,eids=None,sids=None):sections.append({'section':title,'text':text,'evidence_ids':eids or [],'scenario_ids':sids or [],'origin':'DETERMINISTIC'})
    def find(name):return next((e for e in entries if e['metric_name']==name and e['category']=='financial'),None)
    def textfact(e):return fmt(e['value'],e['unit'])+f' ({e["fiscal_period"]["start"] or ""} ~ {e["fiscal_period"]["end"]})' if e else '미확인'
    m=value['market_snapshot'];financial=value.get('scenario_reference');ticker=value['ticker']
    if m.get('security_type')=='ETF':
        add('ETF Market Snapshot',f'거래일 {m.get("trading_date")}, 실제 EOD 종가 {m.get("raw_close")} USD, 상태 {m["data_status"]}. 실시간 가격이 아닙니다.')
        add('Fund Data','보유종목·NAV·보수·AUM·추적지수에 대한 별도 펀드 자료는 연결하지 않았습니다.')
        add('Scope','기업 매출·이익률·P/E 시나리오 및 기업형 Investment Thesis는 적용하지 않습니다.')
        return {'version':VERSION,'ticker':ticker,'memo_type':'ETF','availability':'NOT_APPLICABLE','sections':sections,'interpretations':[],
            'limitations':['펀드 전용 자료 부족. 기업형 메모를 생성하지 않았습니다.'],'semantic_persuasiveness':'NOT_MEASURED'}
    primary=next((e for e in entries if e['category']=='business' and e['metric_name'] in {'cloud','memory','power','energy'}),None)
    name=primary['statement'] if primary else '정규화된 사업 설명 일부만 확인'
    ni,rev,fcf,capex=[find(n) for n in ['ttm_parent_net_income','ttm_revenue','ttm_fcf','ttm_capital_expenditure']]
    if rev is None:rev=next((e for e in entries if e['category']=='financial' and e['metric_name']=='revenue' and e.get('role')=='current'),None)
    available_metrics=[r for r in value['valuation_metrics'] if r['calculation_status']=='AVAILABLE']
    add('Executive Summary',f'{ticker}: {name}. 확인된 매출·이익·현금흐름과 조건부 가치평가를 함께 읽되, 미래 지속성과 전체 투자 매력도는 확정하지 않습니다.',[primary['evidence_id']] if primary else [])
    if primary:add('Business & Financial Drivers',primary['statement']+': '+primary.get('source_text','')[:180]+' (회사 공시 설명)',[primary['evidence_id']])
    latest=value.get('business_segments',[])
    if latest:
        top=max(latest[0]['segments'],key=lambda s:Decimal(s['segment_revenue']))
        add('Business & Financial Drivers',f'최근 공시의 큰 사업부 {top["segment_name"]}: 매출 {fmt(top["segment_revenue"])}, 전년 같은 공시 비교 성장률 {fmt(top["segment_growth_rate"],"percent")}입니다. 개편 전후 또는 개별 제품 성장률과 동일시하지 않습니다.',[e['evidence_id'] for e in entries if e['original_id']==top['id']])
    add('Financial Snapshot',f'선택된 기준 매출 {textfact(rev)}, 지배기업 귀속 순이익 {textfact(ni)}, FCF {textfact(fcf)}, 현금 설비취득 {textfact(capex)}. FCF는 같은 기간의 OCF−현금 CapEx이며 기간이 다른 항목을 합치지 않습니다.',[e['evidence_id'] for e in [rev,ni,fcf,capex] if e])
    current=', '.join(f'{r["metric_name"]} {fmt(r["value"],r["unit"])}' for r in available_metrics)
    comparisons=[r for r in value['relative_valuation'] if r['peer_median'] is not None]
    peer='; '.join(f'{r["metric_name"]}은 {r["eligible_peer_count"]}개 부분 비교 기업의 관측 중앙값 {fmt(r["peer_median"],"percent" if r["metric_name"]=="FCF_YIELD" else "multiple")}' for r in comparisons)
    add('Valuation Interpretation',f'거래일 {m.get("trading_date")}, EOD 기준 종가 {m.get("raw_close")} USD. {current or "계산 가능한 배수 미확인"}. {peer or "지표별 최소 비교군이 부족해 중앙값은 보류"}. 관측 차이는 고평가·저평가나 매수 판정이 아닙니다. 가격·배수는 로컬 계산/렌더링으로만 유지합니다.')
    if review['scenarios']:
        for row in review['scenarios']:
            focus=[f for f in row['assumptions'] if f['assumption_name'] in {'growth','net_margin','exit_pe'}]
            detail='; '.join(f'{f["assumption_name"]}={f["original_assumption"]["assumption_value"]} ({f["support_type"]}/{f["plausibility_status"]})' for f in focus)
            add('Scenario Reality Check',f'{row["name"].upper()}: {detail}. 내부 정합성 {row["coherence"]}. '+ ' '.join(row['additional_conditions']),sids=[row['scenario_id']])
    else:add('Scenario Reality Check','연간 재무 이력 또는 범위가 충분하지 않아 시나리오 현실성 검토를 보류합니다. 없는 시나리오를 새로 만들지 않았습니다.')
    histories=value.get('memo_historical_evidence',[])
    if histories:
        add('Scenario Reality Check','현재 이용 가능한 연차 공시에 표시된 과거 순이익률: '+'; '.join(f'{h["period"]["start"]}~{h["period"]["end"]} {fmt(h["value"],"ratio")} / {h["period_days"]}일' for h in histories)+'. 비교값의 현재 공시 재표시이며 당시 PIT 자료나 장기 평균이 아닙니다. 서로 다른52/53주 기간을 평균내지 않았습니다.',[e['evidence_id'] for e in entries if e['category']=='history'])
    if ticker=='MSFT':
        add('Investment Thesis','공시된 클라우드·AI 관련 사업과 사업부 성장, 실제 현금 창출은 사업 확장 논거의 출발점입니다. 다만 최근 분기의 성장률을 미래 연간 성장으로 이어가는 것은 외삽입니다.',[primary['evidence_id']] if primary else [])
        add('Counter-Thesis','성장과 높은 수익성이 이어져도 설비투자·운전자본 부담이 현금 회수를 늦추거나 시장 배수가 낮아질 수 있습니다. 현재 배수 유지를 회사 가이던스처럼 취급할 수 없습니다.',[e['evidence_id'] for e in [fcf,capex] if e])
    elif ticker=='MU':
        add('Investment Thesis','DRAM/NAND/HBM 사업과 최근 높은 이익은 실제 공시 근거입니다. 그러나 낮은 관측 P/E가 지속 가능한 이익의 할인인지, 순환적 고점 이익의 효과인지는 현재 자료로 확정할 수 없습니다.',[e['evidence_id'] for e in [primary,ni] if e])
        add('Counter-Thesis','기본 시나리오의 순이익률 정상화는 최근 마진을 절반으로 낮춘 연구 가정입니다. 실제 비교 연간 공시가 있어도 이를 역사 평균이라고 부르지 않습니다. FY2026은371일/53주이며 수급·가격·공급 확대·CAPEX 변화를 추가 확인해야 합니다.',[e['evidence_id'] for e in [ni,capex] if e],['S_base'] if review['scenarios'] else [])
    elif ticker=='BE':
        add('Investment Thesis','연료전지·전력 공급 사업의 매출 성장과 순이익, 실제 영업현금흐름·설비취득을 함께 점검할 수 있습니다. 성장·마진·종료 배수의 충격은 연구용 가정입니다.',[primary['evidence_id']] if primary else [])
        add('Counter-Thesis','높은 관측 배수만으로 미래 성장이나 현금 회수가 정당화되지 않습니다. 자본 조달·희석·투자비의 미래 조건을 확정하지 않고, 계약 한도·수주를 매출이나 차입 원금으로 대체하지 않습니다.',[e['evidence_id'] for e in [fcf,capex] if e])
    else:
        add('Investment Thesis','현재 저장된 사업·재무 자료에서 확인된 부분만 제공합니다. XOM의 이전/후속 CIK 및 연간 연결 범위를 검증하지 않고 역사 재무를 합치지 않습니다.',[primary['evidence_id']] if primary else [])
        add('Counter-Thesis','사업 설명이 존재해도 연간 이력·정량 시나리오·동일 범위 비교가 부족하면 투자 매력도 판단은 제한됩니다. 위험 공시 coverage가 부족한 부분도 미확인입니다.')
    add('Monitoring Checklist','후속 같은 기간의 매출·사업부 성장과 조직 변경, 지배기업 순이익률, OCF와 현금 설비취득의 차이, 수급·고객 수요·투자 회수 조건을 확인합니다. 이후 관측이 가정과 다르면 논거를 재검토하되 과거 P4를 덮어쓰지 않습니다.')
    add('Key Uncertainties','미래 가이던스는 직접 확인되지 않았습니다. 배수·수익성·CAPEX 가정의 발생 확률, 경제적 설득력과 투자 성과는 NOT_MEASURED입니다. ETF/누락 재무/PIT/기업범위 제한과 원문 충돌 및 경제적 상충 신호를 구분합니다.')
    add('Sources & Methodology','SEC 사실/현재 공시 비교값, 기존 P4 계산, 명시적 연구 가정, 제안된 해석을 분리합니다. 상세 수치·원문 위치·가정은 동일한 RESULT.json 및 Detailed Research Report와 공유합니다. 새로운 수치 계산을 LLM에 맡기지 않았습니다.')
    return {'version':VERSION,'ticker':ticker,'memo_type':'CORPORATE','availability':pack['availability'],'sections':sections,
        'interpretations':value.get('ai_synthesis',{}).get('accepted_claims',[]),
        'limitations':value['memo_limitations'],'semantic_persuasiveness':'NOT_MEASURED'}


def enrich(value,snapshot,*,ai=False,dry_run=False,saved=None,ai_mode='roles',ai_context='primary'):
    value['memo_historical_evidence']=evidence_pack.historical_references(value,snapshot)
    value['evidence_pack']=evidence_pack.build(value)
    value['scenario_review']=scenario_review.build(value,value['evidence_pack'])
    ctx=grounding.build(value,ai_context);value['pair_context']=ctx;value['evidence_pairs']=ctx['pairs']
    view=grounding.model_view(value,ctx,grounding.ROLES)
    value['model_input_summary']={'policy':'SEC_ONLY_NO_VENDOR_INPUT','included_evidence_ids':[e['id'] for e in view['evidence']],
        'excluded_categories':['market','valuation','relative','peer','scenario_equity','exit_multiple','news_metadata'],
        'pair_count':len(ctx['pairs']),'contains_private_paths':False,'token_measurement':'AT_EXPLICIT_AI_RUNTIME_ONLY','external_calls':0}
    value['memo_limitations']=['자동 source/수치 검사는 독립 경제 의미 검증이나 투자 성과 검증이 아닙니다. 기존 SHADOW/23요인/시나리오는 변경하지 않았습니다.']
    value['ai_requested']=ai;value['ai_dry_run']=dry_run;value['ai_mode']=ai_mode;value['ai_context']=ai_context
    if saved is not None:
        record=saved;assessment=synthesis.assess(record,value,ctx)
    elif ai and not dry_run and value['market_snapshot'].get('security_type')!='ETF':
        record=synthesis.run(value,ctx,ai_mode);assessment=synthesis.assess(record,value,ctx)
    else:
        record=None;assessment={'synthesis_mode':'AI_NOT_RUN' if ai or dry_run else 'DETERMINISTIC','accepted_claims':[],
            'claim_validation':[],'actual_model_execution':False,'accepted_count':0,'rejected_count':0,'semantic_accuracy':'NOT_MEASURED'}
    value['ai_record']=record;value['ai_synthesis']=assessment;value['synthesis_mode']=assessment['synthesis_mode']
    if 'provenance' in value:value['provenance']['model_calls']=record['request_attempts'] if record else 0
    value['claim_validation']=assessment['claim_validation']
    value['ai_provider_status']={'provider':'existing_pinned_local_qwen_cuda','status':record['execution_status'] if record else 'AI_NOT_RUN',
        'reason':record.get('reason') if record else 'DRY_RUN' if dry_run else 'NOT_REQUESTED_OR_NOT_APPLICABLE',
        'model':llm_adapter.MODEL if record else None,'external_calls':0,'paid_cost_usd':'0',
        'input_sha256':ctx['sha256'],'requests':record['request_attempts'] if record else 0}
    value['investment_memo']=compose(value)


def render(value):
    memo=value['investment_memo'];pack=value['evidence_pack'];entry={e['evidence_id']:e for e in pack['entries']}
    lines=[f'# {value["ticker"]} — '+('ETF 자료 요약' if memo['memo_type']=='ETF' else 'Investment Memo'),'',
        f'기준 {value["analysis_as_of"]} · 방식 {value["synthesis_mode"]} · 자료 {memo["availability"]}',
        '수치·회사 공시·연구 가정·해석을 구분합니다. 투자 점수·확정 목표가·매매 신호는 미산출입니다.','']
    titles=list(dict.fromkeys(s['section'] for s in memo['sections']))
    for i,title in enumerate(titles,1):
        lines+=['## '+str(i)+'. '+title,'']
        for s in memo['sections']:
            if s['section']!=title:continue
            lines += [escape(s['text'])+(' ['+', '.join(s['evidence_ids'])+']' if s['evidence_ids'] else ''),'']
    accepted=memo['interpretations']
    if accepted:
        lines+=['## AI-Assisted — 근거에 연결된 해석 제안','',
            '실제 로컬 모델 응답 중 자동 source/역할·수치 검사 범위를 통과한 제안입니다. 경제적 설득력은 독립 검증되지 않았습니다. 시장가격·배수는 모델에 전달하지 않았습니다.','']
        for claim in accepted:
            lines += [f'- **{claim["claim_type"]}**: '+escape(claim['statement'])+' ['+', '.join(claim['supporting_evidence_ids'])+']',
                '  조건: '+escape(claim['assumptions'])+' / 한계: '+escape(claim['limitations'])]
            for identifier in claim['supporting_evidence_ids']:
                e=entry[identifier]
                if e['value'] is not None:lines.append('  결정론적 근거 수치 '+identifier+': '+e['metric_name']+' '+fmt(e['value'],e['unit'])+'; '+str(e['fiscal_period']))
            for identifier,sources in claim.get('exact_sources',{}).items():
                for source in sources:
                    lines+=['  '+identifier+' 원문: '+escape(source['quote'])+' [SEC]('+source['url']+')']
        info=value['ai_synthesis']
        lines+=['','AI 사용 역할: '+', '.join(info.get('accepted_roles',[]))+' / 결정론적 보완 역할: '+', '.join(info.get('fallback_roles',[]))+'.',
            '일부 역할 수락은 전체 AI 품질 관문 통과나 독립 의미 검증을 뜻하지 않습니다.']
    lines+=['','### 근거 찾기','']
    cited=list(dict.fromkeys(i for s in memo['sections'] for i in s['evidence_ids']))
    cited+=list(dict.fromkeys(i for c in accepted for i in c['supporting_evidence_ids'] if i not in cited))
    for identifier in cited:
        e=entry[identifier];sources=[value['source_references'][r] for r in e['source_refs']]
        urls=list(dict.fromkeys(s['url'] for s in sources))
        lines.append('- '+identifier+': '+e['statement']+' / '+e['evidence_kind']+' / '+' '.join('[SEC 원문]('+u+')' for u in urls))
    lines+=['','[상세 재무·사업·시나리오 보고서](REPORT.ko.md) · 입력·가정·원문 locator·원본 hash: RESULT.json / INPUT.json.',
        'AI가 실행되지 않았거나 응답이 기각되면 결정론적 메모를 유지합니다. 원래 P4 결과와 모든 과거 평가를 덮어쓰지 않습니다.']
    return '\n'.join(lines)+'\n'
