"""Readable Korean research, with evidence facts and unverified analyst opinion apart."""
from html import escape
import re
from . import openai_provider as provider
from .professional_schema import SECTIONS

TITLES={'EXECUTIVE_SUMMARY':'Executive Summary','COMPANY_OVERVIEW':'Company Overview',
 'INVESTMENT_THESIS':'Investment Thesis','BUSINESS_ECONOMICS':'Business & Competitive Position',
 'FINANCIAL_PERFORMANCE':'Financial Performance','CASH_FLOW_QUALITY':'Earnings Quality & Cash Flow',
 'CAPITAL_ALLOCATION':'Capital Allocation / CAPEX','INDUSTRY_CYCLICALITY':'Industry & Cyclicality',
 'VALUATION_SCENARIO':'Valuation Scenario Assessment','BULL_CASE':'Bull Case','BEAR_CASE':'Bear Case',
 'COUNTER_THESIS':'Counter-Thesis','KEY_RISKS':'Key Risks','FINAL_ASSESSMENT':'Final Research Assessment'}


def safe(text):
    return escape(str(text)).replace('|','\\|')


def evidence_text(e):
    value=e['value']
    if value is not None:
        p=e['period'] or {};period=(p.get('start') or '')+' ~ '+str(p.get('end','UNKNOWN'))
        return e['metric']+': '+str(value)+' '+str(e['unit'])+' / '+period+' / '+e['accounting_scope']
    return e.get('text') or ('동일 원문 '+e['text_reference'] if e.get('text_reference') else 'UNKNOWN')


def filled(text,entries):
    return safe(re.sub(r'\{\{(E\d+)\}\}',lambda m:evidence_text(entries[m[1]])+' ['+m[1]+']',text))


def outputs(value):
    p=value['professional_research'];r=p['record'];a=p['validation']
    usage={'model':p['model'],'reasoning_effort':p['config']['reasoning'],'origin':r['origin'],
           'status':r['status'],'actual_api_usage':r['usage'] if r['origin']=='OPENAI_API' else None,
           'illustrative_mock_usage':r['usage'] if r['origin']=='ILLUSTRATIVE_MOCK_NOT_LIVE' else None,
           'api_requests_this_run':p['api_requests_this_run'],'stored_api_requests':p['stored_api_requests'],
           'actual_billed_usd':None}
    costs={'currency':'USD','price_profile':provider.profile()['price_per_million'],
        'price_checked_date':provider.profile()['price_checked_date'],'estimate':r['estimate'],
        'estimated_usd_from_actual_usage':r['estimated_usage_usd'] if r['origin']=='OPENAI_API' else None,
        'actual_invoice_usd':None,'billing_verified':False,'reasoning_in_output_tokens':True,
        'budget_receipts':[x['budget'] for x in r['attempts']],
        'note':'Usage-based estimate, not an invoice. Unknown outcomes retain reservations. No API request in dry-run.'}
    result={'ticker':value['ticker'],'model':p['model'],'mode':p['mode'],'origin':r['origin'],'provider_status':r['status'],
       'research_state':p['research_state'],'verified_state':p['verified_state'],'verified_claims':[],
       'drafts':a['drafts'] if p['mode']=='research' else [],'monitoring_framework':[m['value'] for m in a['monitoring'] if m['hard_status']=='HARD_PASS'] if p['mode']=='research' else [],
       'economic_usefulness':'NOT_MEASURED','independent_semantic_accuracy':'NOT_MEASURED'}
    return {'OPENAI_MODEL_PROFILE.json':provider.profile(),'RESEARCH_PACKET.json':p['packet'],
            'API_RECORD.json':r,'AI_RESEARCH_RESULT.json':result,'API_USAGE.json':usage,
            'API_COST_REPORT.json':costs,'VALIDATION_RESULTS.json':a}


def render(value):
    p=value['professional_research'];packet=p['packet'];r=p['record'];a=p['validation']
    entries={e['evidence_id']:e for e in packet['evidence']};visible=a['drafts'] if p['mode']=='research' else []
    lines=[f'# {safe(packet["company"]["name"])} ({value["ticker"]}) 전문 리서치','',
       f'분석 기준: {value["analysis_as_of"]} · 모델: {p["model"]} · reasoning: {p["config"]["reasoning"]} · 모드: {p["mode"]}',
       f'실행: {r["status"]} · 출처: {r["origin"]} · Research: {p["research_state"]} · Verified: {p["verified_state"]}',
       f'시장 자료: {packet["freshness"]["market_data_status"]}. 시세·가격 배수·가격 파생 가치는 모델에 제공하지 않았습니다.',
       '관측 사실은 SEC/Python 자료입니다. AI_ANALYST_INTERPRETATION은 의미 검토 전 연구 의견이며 VERIFIED 금융 사실이 아닙니다.',
       '독립 의미 정확도·경제적 유용성: NOT_MEASURED. [기존 결정론적 메모](MEMO.ko.md) / [출처·검사](VALIDATION_RESULTS.json).','']
    if value['is_illustrative'] or r['origin']=='ILLUSTRATIVE_MOCK_NOT_LIVE':lines+=['**ILLUSTRATIVE — NOT LIVE MARKET DATA / 실제 GPT 생성물이 아닙니다.**','']
    if not visible:lines+=['AI 분석이 없거나 현재 모드에서 승격되지 않았습니다. 기존 결정론적 메모를 유지합니다. 실제 GPT의 금융 추론 성공으로 집계하지 않습니다.','']
    for section in SECTIONS[:-1]:
        lines+=['## '+TITLES[section],'']
        if section=='COMPANY_OVERVIEW':lines+=[f'CIK: {safe(packet["company"]["cik"])} · 공시 산업: {safe(packet["company"]["industry"])}.','']
        if section=='FINANCIAL_PERFORMANCE':
            lines+=['[Observed / Python Calculated] 기간·단위·귀속을 원본 그대로 표시합니다.','',
                    '| 근거 | 관측/계산 | 범위 | 기간 일수 |','|---|---|---|---:|']
            for e in packet['evidence']:
                if e['value'] is not None:lines.append('| '+e['evidence_id']+' | '+safe(evidence_text(e))+' | '+e['financial_scope']+' | '+str(e['period_days'])+' |')
            lines.append('')
        claims=[c for c in visible if c['section']==section]
        for c in claims:lines+=claim_lines(c,entries)
        if not claims:lines+=['해당 AI 분석은 미생성·미승인 또는 근거 부족입니다.','']
        if section=='VALUATION_SCENARIO':lines+=[safe(packet['valuation']['reason']),'']
    lines+=['## Monitoring Checklist','']
    monitoring=[m['value'] for m in a['monitoring'] if m['hard_status']=='HARD_PASS'] if p['mode']=='research' else []
    for m in monitoring:
        lines += ['- '+filled(m['indicator'],entries),'  - 확인: '+filled(m['source_or_method'],entries),
                  '  - 강화: '+filled(m['strengthening'],entries),'  - 약화: '+filled(m['weakening'],entries),
                  '  - 경제적 이유: '+filled(m['economic_reason'],entries)+' / '+', '.join(m['evidence_ids'])]
    if not monitoring:lines.append('검사를 통과한 AI 관찰 항목이 없습니다. 결정론적 메모의 점검 항목을 확인합니다.')
    lines+=['','## Evidence & Sources','']
    for e in packet['evidence']:
        lines+=['- '+e['evidence_id']+' / '+e['evidence_type']+' / '+e['business_scope']+' / '+safe(evidence_text(e))]
        for key in e['source_keys']:
            s=packet['sources'][key]
            lines+=['  - [SEC 원문]('+s['url']+') · 제출 '+s['filing_date']+' · 이용 가능 '+s['available_at']+' · SHA `'+s['source_hash']+'` · locator '+safe(s['source_locator'])]
    lines+=['','## Uncertainty / Missing Information','']
    lines+=['- '+safe(t) for t in packet['missing_information']]
    if r['response'] and a['schema_status']=='PASS' and not a.get('global_issues'):
        lines+=['- '+filled(t,entries) for t in r['response']['uncertainties']+r['response']['data_limitations']]
    lines+=['','## Final Research Assessment','']
    for c in visible:
        if c['section']=='FINAL_ASSESSMENT':lines+=claim_lines(c,entries)
    lines+=['정식 메모의 검증 AI 반영은 '+str(p['verified_claims'])+'건입니다. 모델 성능·투자 수익률·독립 정확도가 검증됐다는 뜻이 아닙니다.',
            '기존 23요인·P4 산식·SHADOW·점수와 목표가 보류를 유지합니다.','']
    return '\n'.join(lines)


def claim_lines(c,entries):
    return ['### '+c['claim_id']+' · '+c['claim_type'],'',
            '[AI_ANALYST_INTERPRETATION / SEMANTIC_REVIEW_NEEDED] '+filled(c['statement'],entries),
            '- 경제 경로: '+filled(c['economic_mechanism'],entries),'- Assumption: '+filled(c['condition'],entries),
            '- 반증 조건: '+filled(c['invalidation'],entries),'- Unverified: '+filled(c['uncertainty'],entries),
            '- 근거: '+', '.join(c['evidence_ids'])+' / 결론 범위 '+c['scope'],'']
