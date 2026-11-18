"""Separate observed facts, visible drafts, rejected diagnostics and verified fallback."""
import html,json

def outputs(value):
    p=value['research_pipeline'];a=p['assessment'];visible=a['drafts'] if p['usage_mode']=='research' else []
    return {'AI_RESEARCH.json':{'ticker':value['ticker'],'model':p['model'],'analysis_as_of':value['analysis_as_of'],'usage_mode':p['usage_mode'],
       'status':p['research_state'],'drafts':visible,'verified':False,'independent_semantic_accuracy':'NOT_MEASURED'},
      'AI_VALIDATION.json':a,'AI_RECORD.json':p['record'],'TYPED_EVIDENCE.json':p['context']}

def render(value):
    p=value['research_pipeline'];a=p['assessment'];safe=lambda s:html.escape(str(s)).replace('|','&#124;')
    lines=['# '+value['ticker']+' — AI Research Memo','',
      f'모델: Qwen3.5-4B / Q4_K_M / 비사고 · 기준 {value["analysis_as_of"]} · 모드 {p["usage_mode"]}',
      '실험용 모델 선택 완료. 검증된 금융 추론 능력은 NOT_ESTABLISHED. 독립 의미 정확도와 투자 유용성은 NOT_MEASURED.',
      '실제 모델 실행: '+str(a['actual_model_execution'])+' / Research: '+p['research_state']+' / Verified: '+p['verified_state'],
      '', '## 관측 근거 — Python 복원','',
      '| ID | 대상·범위 | 측정 | 값·단위 | 실제 기간 | 종류 |','|---|---|---|---|---|---|']
    for e in p['context']['evidence']:
        if e['value'] is not None:
            lines.append('| '+e['evidence_id']+' | '+safe(e['subject'])+' / '+e['financial_scope']+' | '+e['metric']+' | '+safe(e['value'])+' '+safe(e['unit'])+' | '+safe(e['period'])+' / days='+safe(e['period_days'])+' | '+e['observation_type']+' |')
        for f in e['source_observations']:
            lines.append('| '+e['evidence_id']+' | '+safe(f['subject'])+' / '+f['scope']+' | '+f['metric']+' | '+safe(f['value'])+' '+safe(f['unit'])+' | '+safe(f.get('period','UNKNOWN'))+' / comparison='+safe(f.get('comparison_basis','UNKNOWN'))+' | SOURCE_FACT |')
    lines+=['','## AI가 제안한 조건부 가설 — 검토 전 초안','']
    visible=a['drafts'] if p['usage_mode']=='research' else []
    if not visible:lines+=['표시할 연구 초안이 없습니다. Verified 모드 또는 hard 검증 실패/미실행입니다. 결정론적 [Investment Memo](MEMO.ko.md)를 사용합니다.']
    for row in visible:
        raw=row['interpretation'];lines+=['### '+row['role']+' — AI_RESEARCH_DRAFT / GROUNDING_CHECKED / SEMANTIC_REVIEW_NEEDED','']
        for label,key in [('가설','claim'),('경제적 경로','mechanism'),('성립 조건','condition'),('반대 논거','counter_thesis'),('강화 관측','strengthening'),('반증 관측','weakening'),('불확실성','uncertainty')]:lines.append('- '+label+': '+safe(raw[key]))
        lines+=['- 선택 근거: '+', '.join(raw['business_evidence_ids']+raw['financial_evidence_ids']),
          '- 자동 의미 검토 신호: '+(', '.join(row['semantic_flags']) or '구체적인 의미의 독립 검토 필요')]
    rejected=[r for r in a['rows'] if r['hard_status']=='HARD_REJECT']
    lines+=['','## 검증 상태','',f'정상 연구 목록에서 제외한 hard 기각: {len(rejected)}건. 원래 응답·사유는 개발 진단 AI_RECORD.json / AI_VALIDATION.json에만 보존합니다.',
      'Source ID·단위·범위의 자동 확인은 경제적 인과의 증명이 아닙니다. 휴리스틱 실패는 자동 성공으로 바뀌지 않습니다.',
      'Verified 보고서 반영:0건. 자유 형식 경제 해석의 필요한 의미 검증이 확보되지 않아 결정론적 fallback입니다.',
      '', '## 정확한 원문과 위치','']
    cited=list(dict.fromkeys(i for r in visible for i in r['interpretation']['business_evidence_ids']+r['interpretation']['financial_evidence_ids']))
    for id in cited:
        for q in p['context']['exact_sources'][id]:lines+=['- '+id+' [SEC 원문]('+q['url']+') / SHA `'+q['raw_sha256']+'` / 위치 `'+json.dumps(q['locator'],ensure_ascii=False)+'`',
            '  > '+safe(q['quote'])]
    lines+=['','관측값·source 위치·UNKNOWN: TYPED_EVIDENCE.json. 전사/제품/고객 수익을 구분하며 원래 P4 계산·23요인·SHADOW·점수 공개 보류는 유지합니다.']
    return '\n'.join(lines)+'\n'
