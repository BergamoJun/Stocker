"""Korean Markdown and immutable personal-research records."""
from decimal import Decimal
from html import escape
from pathlib import Path
import uuid
from research.local_files import atomic_json
from research.sec_refresh import read
from research.sec_report import ROOT
from research.validation import digest
from . import core, methods

LABELS = {'revenue': '매출', 'operating_income': '영업손익', 'net_income': '순손익', 'cash': '현금 및 현금성자산',
    'assets': '총자산', 'liabilities': '총부채(이자부 차입금과 다름)', 'operating_cash_flow': '영업현금흐름',
    'capital_expenditure': '현금 설비취득 지출', 'diluted_eps': '희석 EPS',
    'operating_margin_percent': '영업이익률', 'free_cash_flow': 'FCF(영업현금흐름−현금 설비취득)',
    'cash_to_total_liabilities': '현금/총부채', 'liabilities_to_assets': '총부채/총자산'}
BASES = {'quarter': '분기', 'annual': '연간', 'cumulative_ytd': '연초 누적', 'instant': '기말 잔액'}


def safe(text):
    return escape(str(text)).replace('|', '\\|').replace('\n', ' ')


def number(d):
    if d['value'] is None: return '미확인'
    n = Decimal(d['value'])
    return f'{n / Decimal(1000000):,.2f} 백만 USD' if d['unit'] == 'USD' else f'{n:,.4f} {d["unit"]}'


def valuation_lines(value):
    m=value.get('market_snapshot',{});lines=[]
    if m.get('raw_close') is not None or m.get('adjusted_close') is not None:
        lines += [f'**Market Snapshot** · {safe(m["resolved_ticker"])} / {m["security_type"]} / {m["exchange"]} / {m["currency"]}',
            f'- 거래일: {m["trading_date"]}, 정규장 종료: {m["market_session_end"]}. 일별 종가이며 실시간 시세가 아닙니다.',
            f'- 실제 종가(raw): {m.get("raw_close") or "미확보"} USD · 분할 조정 종가: {m.get("adjusted_close") or "미확보"} USD. 배당조정 가격과 구분합니다.',
            f'- 거래량: {m["volume"]} · 제공처: Massive · 신선도: {m["data_status"]}',
            f'- 취득: {m["retrieved_at"]} · 최초 이용: {m["source_available_at"]} · 증권 ID: {m["security_id"]}',
            f'- 제공처 시가총액: {m.get("provider_reported_market_cap") or "미확인"} USD (reference-date {m.get("market_cap_reference_date")}). 가중평균 EPS주식 수로 재계산하지 않았습니다.']
    else:lines += ['시장 자료 상태: '+safe(m.get('data_status','UNAVAILABLE'))+'. 가격을 임의 생성하지 않았습니다.']
    if m.get('acquisition_error'):lines += ['추가 취득 실패: '+safe(m['acquisition_error'])+'. 기존 가격은 오래된 자료로 표시하며 현재 가치평가에 사용하지 않았습니다.']
    if m.get('security_type')=='ETF':lines += ['ETF 가격 중심 보고서입니다. 기업용 PER·EV/EBITDA를 적용하지 않으며 NAV·보유자산 정보는 미연결입니다.']
    lines += ['','| Metric | Value | Basis | Status |','|---|---:|---|---|']
    for d in value.get('valuation_metrics',[]):
        n=f'{Decimal(d["value"]):,.4f}' if d['value'] is not None else 'N/M' if d['calculation_status']=='NOT_MEANINGFUL' else '미확인'
        lines.append(f'| {d["metric_name"]} | {n} | {safe(d["formula"])} | {d["calculation_status"]} |')
        if d['fiscal_period']:lines.append(f'| 재무기간 | {d["fiscal_period"]["start"] or ""} ~ {d["fiscal_period"]["end"]} | {d["fiscal_period"]["basis"]} | |')
    lines += ['','P/E는 선택된 경로의 주가/이익 또는 시가총액/지배기업 귀속 이익 배수입니다. P/S는 매출, P/B는 보통주 자기자본 대비 가격이며 FCF Yield는 현금창출 규모의 비율입니다. 성장·기간·회계 범위와 함께 읽어야 합니다. 과거·동종기업 비교가 없어 배수만으로 싸거나 비싸다고 판단하지 않습니다.',
              '총부채를 이자부 차입금으로 대체하지 않았습니다. EBITDA는 지원될 때만 영업손익+D&A의 단순 정의이며 기업 발표 조정 EBITDA와 다릅니다.']
    if value.get('missing_inputs'):lines.append('미확인 입력: '+safe(', '.join(value['missing_inputs'])))
    return lines


def _render_v2(v):
    who = v['issuer']; title = who['name'] if who else v['ticker']; refs = {k: 'S' + str(n) for n, k in enumerate(v['source_references'], 1)}
    cite = lambda d: ', '.join(refs[k] for k in d['source_refs']) or '—'
    current = [f for f in v['facts'] if f['role'] == 'current']
    valid = [f for f in current if f['value'] is not None]
    lines = [f'# {safe(title)} ({v["ticker"]}) 개인용 리서치', '', f'분석 기준: {v["analysis_as_of"]} · 전체 상태: {v["analysis_status"]}', '',
        '## 1. Executive Summary', '']
    if v['is_illustrative']: lines.insert(2,'ILLUSTRATIVE — NOT LIVE MARKET DATA')
    if not valid:
        lines += [('시장 증권은 식별했으나 핵심 SEC 재무자료는 미확보입니다. 가격 중심의 부분 보고서를 제공합니다.' if v.get('market_snapshot',{}).get('resolved_ticker') else '기준시각 전에 이용 가능한 핵심 재무자료 또는 종목 식별을 확보하지 못했습니다. 수치와 투자 논거를 생성하지 않았습니다.'),
                  '자료 상태: ' + safe(v['error'] or '원문 재무자료 미확보')]
    else:
        lines += [f'등록 기업: {safe(who["name"])} · CIK {who["cik"]} · 공시 산업 분류: {safe(who["industry"])}.',
                  '재무 현황은 자동 원문 대조로 분석했습니다. 가격·뉴스·정밀 계약 영향의 부족은 아래 해당 부분만 제한합니다.']
        latest = max(f['period']['end'] for f in valid)
        for name in ['revenue', 'operating_income', 'net_income']:
            f = next((f for f in valid if f['name'] == name and f['period']['end'] == latest), None)
            if f: lines.append(f'- {BASES[f["period"]["basis"]]} {LABELS[name]}: {number(f)} ({f["period"]["end"]}, {f["scope"]}; {cite(f)}).')
    lines += ['', '## 2. Financial Snapshot', '', '금액은 백만 USD, EPS는 USD/shares입니다. 관측값은 공시 사실이며 미래 전망이 아닙니다.', '',
              '| 공시 | 항목 | 관측값 | 회계기간 | 귀속 범위 | 근거 |', '|---|---|---:|---|---|---|']
    for f in current:
        p = f['period']; period = (p['start'] + ' ~ ' if p['start'] else '') + p['end'] + ' / ' + BASES[p['basis']]
        lines.append(f'| {f["form"]} | {LABELS[f["name"]]} | {number(f)} | {period} | {f["scope"] or "미확인"} | {cite(f)} |')
    lines += ['', '## 3. Growth & Profitability', '', '| 지표 | 결과 | 기간 | 근거 |', '|---|---:|---|---|']
    for d in v['derived_metrics']:
        if 'growth' not in d['name'] and d['name'] != 'operating_margin_percent': continue
        label = LABELS.get(d['name'], LABELS.get(d['name'].replace('_growth_percent', ''), d['name']) + ' 전년 동기 성장률')
        lines.append(f'| {label} | {number(d)} | {d["period"]["start"] or ""} ~ {d["period"]["end"]} | {cite(d)} |')
        if d['value'] is None and d.get('sign_transition'):
            lines.append(f'| 성장률 보류 사유 | 전년 값이0 이하: {d["sign_transition"]} | 절대 변화 {d["absolute_change"]} | {cite(d)} |')
    lines += ['', '## 4. Financial Health', '', '| 지표 | 결과 | 기준일/기간 | 근거 |', '|---|---:|---|---|']
    for d in v['derived_metrics']:
        if 'growth' in d['name'] or d['name'] == 'operating_margin_percent': continue
        lines.append(f'| {LABELS.get(d["name"],d["name"])} | {number(d)} | {d["period"]["start"] or ""} ~ {d["period"]["end"]} | {cite(d)} |')
    lines += ['','ROE·ROIC·유동비율·이자보상배율·순차입금은 필요한 분모/차입금 범위가 연결되지 않아 보류합니다. 총부채를 차입금으로 대체하지 않습니다.',
              '', '## 5. Valuation', '', *valuation_lines(v),
              '', '## 6. Events & Industry Context', '', v['optional_engine_statuses']['news']['reason'] + '.',
              'ResearchEngine는 기존 SHADOW 품질 상태를 유지합니다. 이번 실행에서 모델을 호출하거나 실제 사건의 손익·주가 영향을 확정하지 않았습니다.',
              '', '## 7. Company-Specific Risks', '']
    risks = []
    for name in ['operating_income', 'net_income']:
        observations = [f for f in valid if f['name'] == name]
        if observations:
            f = max(observations, key=lambda f: f['period']['end'])
            risks.append(f'{f["period"]["end"]} {LABELS[name]} {number(f)}: ' + ('손실의 지속 여부를 확인해야 합니다.' if Decimal(f['value']) < 0 else '현재 양의 손익이 미래에도 유지되는지는 별도 확인이 필요합니다.') + f' ({cite(f)})')
    capex = [f for f in valid if f['name'] == 'capital_expenditure']
    if capex:
        f = max(capex, key=lambda f: f['period']['end']); risks.append(f'현금 설비취득 지출 {number(f)}의 향후 회수와 현금흐름을 확인해야 합니다. ({cite(f)})')
    if any(f.get('scope') == 'consolidated_sales_excluding_equity_affiliate_and_other_income' for f in valid):
        risks.append('매출은 지분법 이익·기타이익을 제외한 범위입니다. 총수익과 혼합한 이익률을 계산하지 않았습니다.')
    lines += ['- ' + x for x in risks] or ['- 종목별 재무 위험을 판단할 확인 자료가 부족합니다.']
    lines += ['- 계약별 금리 노출과 헤지 비율은 미확인입니다. 시설 한도·총부채·swap notional을 실제 변동금리 원금으로 해석하지 않습니다.',
              '', '## 8. Bull / Base / Bear', '']
    lines += [f'- {d["name"].upper()} [가정]: {d["value"]}. 정량 목표·확률 없음.' for d in v['estimates']] or ['자료 부족으로 시나리오를 작성하지 않았습니다.']
    growth = [d for d in v['derived_metrics'] if d['name'] == 'revenue_growth_percent' and d['value'] is not None]
    thesis = ('가격 대비 일부 재무 배수는 계산했지만 비교 평가·산업 맥락이 부족해 전체 투자 매력도 판단은 유보합니다.' if v.get('valuation_status')=='PARTIAL' else '정렬된 밸류에이션 입력과 산업 맥락이 부족해 전체 투자 매력도 판단은 유보합니다.')
    if growth:
        d = max(growth, key=lambda d: d['period']['end'])
        thesis = f'{d["period"]["end"]} 매출 성장률 {number(d)}은 ' + ('성장 논거' if Decimal(d['value']) > 0 else '수요·매출 추이의 점검 요인') + f'입니다. ({cite(d)}) ' + thesis
    lines += ['', '## 9. Investment Attractiveness', '', thesis,
              f'기존23요인 중 이번 자료로 지원되는 요인은 {len(v["scoring_status"]["available_factors"])}개입니다. 전체 점수는 미산출이며 가중치를 재배분하지 않았습니다. 기존 모델은 EXPERIMENTAL/SHADOW입니다.',
              '', '## 10. Sources & Uncertainty', '',
              'OBSERVED=원문 관측, DERIVED=출처 입력의 결정론 계산, ESTIMATED=명시적 가정, UNKNOWN=미확인입니다. 미확인은0이 아닙니다.']
    for ref, key in refs.items():
        s = v['source_references'][ref]
        if s.get('kind')=='market':
            lines.append(f'- {key}: [Massive EOD/reference]({s["url"]}), 거래일 {s["trading_date"]}, 취득 {s["retrieved_at"]}, 최초 이용 {s["available_at"]}; SHA-256 `{s["raw_sha256"]}`.');continue
        loc = s['locator']
        lines.append(f'- {key}: [{s["accession"]}]({s["url"]}), 제출 {s["filed_date"]}, 최초 저장 {s["available_at"]}; {safe(loc.get("tag"))}, context={safe(loc.get("context_ref"))}, row={loc.get("row_number")}, SHA-256 `{s["raw_sha256"]}`.')
    lines += ['- ' + safe(x) for x in v['limitations']]
    for f in current:
        if f['value'] is None: lines.append(f'- {f["form"]} {f["period"]["end"]} {LABELS[f["name"]]}: '+safe(f['limitations'][0]))
    for error in v['errors']: lines.append('- 처리 제한: ' + safe(error['code']))
    return '\n'.join(lines) + '\n'


def _render_v3(v):
    """Preserve financial tables, add source-bound business context in fourteen sections."""
    if 'business_profile' not in v:return _render_v2(v)
    import re
    old=_render_v2(v)
    parts=re.split(r'^## \d+\. ',old,flags=re.M)
    sections={p.split('\n',1)[0]:p.split('\n',1)[1] for p in parts[1:]}
    facts={x['id']:x for x in v['business_profile']}
    sources=v['source_references']
    def citations(ids):
        return ' '.join(f'[{safe(i)}]({sources[i]["url"]})' for i in ids if i in sources)
    def statements(ids):
        lines=[]
        for identifier in list(dict.fromkeys(ids))[:4]:
            x=facts[identifier]
            lines.append(f'- [회사 공시 / SOURCE_FACT] {x["statement"]}: “{safe(x["source_text"][:210])}…” {citations(x["source_refs"])}')
        return lines or ['확인 가능한 원문 근거가 부족합니다. 해당 내용을 추정하지 않았습니다.']
    lines=[parts[0].rstrip(),'','## 1. Executive Summary',sections['Executive Summary'].strip()]
    if facts:
        primary=[x['id'] for x in facts.values() if x['topic'] in {'cloud','software','memory','power','energy'} and x['section']!='RISK_FACTORS']
        lines+=statements(primary[:1])
        lines+=['사업 설명과 위험 요인은 회사의 공시입니다. 독립 검증된 경쟁력·시장점유율·미래 성장률을 뜻하지 않습니다.']
    lines+=['','## 2. Market & Valuation Snapshot',sections['Valuation'].strip(),
        '','## 3. Financial Performance',sections['Financial Snapshot'].strip(),sections['Growth & Profitability'].strip(),sections['Financial Health'].strip(),
        '','## 4. Business Overview']
    lines+=statements([x['id'] for x in facts.values() if x['section']=='BUSINESS'] or [x['id'] for x in facts.values() if x['section']=='MD&A'])
    lines+=['','## 5. Segment Performance']
    groups=v.get('business_segments',[])
    if not groups:lines+=['사업부 재무 숫자를 확정하지 못했습니다. 제품·지역 차원을 사업부로 대체하지 않았습니다.']
    for g in groups[:2]:
        lines += [f'공시 {g["accession"]}: {g["period"]["start"]} ~ {g["period"]["end"]}. 매출 합계 대조: {g["reconciliation_status"]}.',
            '', '| 사업부 (공시 taxonomy) | 매출 백만 USD | 전년 매출 백만 USD | 성장률 % | 영업이익률 % | 근거 |', '|---|---:|---:|---:|---:|---|']
        for x in g['segments']:
            money=lambda n:f'{Decimal(n)/1000000:,.2f}' if n is not None else '미확인'
            pct=lambda n:f'{Decimal(n):,.2f}' if n is not None else '미확인'
            lines.append(f'| {safe(x["segment_name"])} | {money(x["segment_revenue"])} | {money(x["prior_period_segment_revenue"])} | {pct(x["segment_growth_rate"])} | {pct(x["segment_profit_margin"])} | {citations(x["source_refs"])} |')
    lines+=['사업부 합계에는 내부거래·본사 조정이 있을 수 있습니다. RECONCILIATION_REQUIRED는 연결 매출과 같다고 확정하지 못한 상태입니다. 사업부 개편 전후나 Azure와 전체 클라우드 사업부를 동일시하지 않습니다.',
        '','## 6. Growth Drivers',*statements(v['growth_drivers']),
        '','## 7. Competitive Position',*statements(v['competitive_factors']),
        '경쟁력은 회사 주장으로 한정합니다. 근거 신뢰도는 원문 연결에 관한 것이며 경쟁 승리나 수익률의 확률이 아닙니다.',
        '','## 8. Industry Environment',*statements(v['industry_context']),
        '산업 성장률·점유율·확정 CAGR은 미확인입니다. 원문은 특정 기업의 시각이며 산업 전체의 독립 통계가 아닙니다.',
        '','## 9. Recent Events','기존 뉴스 metadata는 기사 본문과 영향의 확인이 아닙니다. 이번 오프라인 분석에서는 확인된 최근 사건을 추가하지 않았습니다.',
        '','## 10. Investment Thesis']
    all_finance={x['id']:x for x in v['facts']}
    def theses(items):
        out=[]
        for x in items:
            out.append('- [분석적 해석] '+x['statement']+' '+citations(x['source_refs']))
            for identifier in x['financial_context']:
                f=all_finance[identifier];out.append(f'  근거 재무: {LABELS.get(f["name"],f["name"])} {number(f)}, {f["period"]["start"]} ~ {f["period"]["end"]}.')
            out.append('  불확실성: '+x['uncertainty'])
        return out or ['사업·재무 근거를 함께 연결할 수 없어 투자 논거를 보류합니다.']
    lines+=theses(v['investment_theses'])
    lines+=['','## 11. Risks & Counter-Thesis',*statements(v['company_risks']),*theses(v['counter_theses']),
        '','## 12. Bull / Base / Bear']
    lines += [f'- {x["name"].upper()} [시나리오 가정]: {x["value"]}' for x in v['estimates']] or ['근거 부족으로 시나리오를 보류합니다.']
    lines+=['','## 13. Valuation Interpretation',
        '성장·사업 구조와 현재 배수를 함께 읽어야 합니다. 비교군과 미래 이익 전망이 없어 적정 배수나 목표주가는 미산출입니다. 기존23요인 가중치와 SHADOW를 유지하며 투자 점수는 공개하지 않습니다.']
    for x in v['valuation_metrics']:
        if x.get('period_classification'):lines.append(f'- {x["metric_name"]}: {x["period_classification"]}, 실제 공시 기간 {x.get("period_days")}일. 최근 회계연도와 정확한12개월 TTM을 구분합니다.')
    for x in v['valuation_coverage']:
        if x['status']=='UNAVAILABLE':lines.append(f'- {x["metric_name"]} 보류: {safe(", ".join(x["missing_inputs"]+x["reason_categories"]))}')
    lines+=['','## 14. Sources & Uncertainty',sections['Sources & Uncertainty'].strip()]
    lines += ['- '+x for x in v['analysis_limitations']]
    lines += ['원문 전체와 추출 문장은 RESULT.json의 source_text 및 source_references locator에서 대조할 수 있습니다. 신규 모델·유료 데이터·목표가·매매 신호는 사용하지 않았습니다.']
    return '\n'.join(lines)+'\n'


def _render_v4(v):
    """Compose P4 sections without discarding the P3 financial/business tables."""
    if 'scenario_results' not in v:return _render_v3(v)
    import re
    old=_render_v3(v);parts=re.split(r'^## \d+\. ',old,flags=re.M)
    sections={p.split('\n',1)[0]:p.split('\n',1)[1].strip() for p in parts[1:]}
    lines=[parts[0].rstrip(),'','## 1. Executive Summary',sections['Executive Summary'],
        'P4 결과는 명시한 가정에 따른 조건부 지분가치입니다. 미래 주가 예측·시장 컨센서스·매수/매도 신호가 아닙니다.']
    equity=[Decimal(x['equity_value']) for row in v['scenario_results'] for x in row['valuations'] if x['equity_value'] is not None]
    if equity:lines.append(f'선택한 시나리오·방법의 조건부 지분가치 범위: {min(equity)/1000000:,.2f} ~ {max(equity)/1000000:,.2f} 백만 USD. 확률 범위나 현재 적정가치가 아닙니다.')
    mapping=[('2. Market Snapshot','Market & Valuation Snapshot'),('3. Financial Performance','Financial Performance'),
        ('4. Business Overview','Business Overview'),('5. Segment Performance','Segment Performance'),('6. Growth Drivers','Growth Drivers'),
        ('7. Competitive Position','Competitive Position'),('8. Industry Environment','Industry Environment')]
    market=sections['Market & Valuation Snapshot'];table_index=market.find('| Metric |')
    for title,original in mapping:
        body=market[:table_index].strip() if title.startswith('2.') and table_index>=0 else sections[original]
        lines+=['','## '+title,body]
    lines+=['','## 9. Current Valuation',market[table_index:] if table_index>=0 else market,
        '','## 10. Relative Valuation',
        '비교 후보는 제품·사업 근거와 자료 정합성을 확인한 뒤 지표별로 포함합니다. 부분 비교군의 중앙값을 산업 평균이나 적정 배수로 해석하지 않습니다.',
        '', '| 후보 | 비교 가능성 | 선정·제외 이유 |', '|---|---|---|']
    for p in v['peer_candidates']:lines.append(f'| {p["peer_ticker"]} | {p["comparability_status"]} | {safe(p["key_differences"])} |')
    lines+=['','| 지표 | 현재 | 비교군 중앙값 | 관측 차이 | 사용 후보/최소 | 상태 |','|---|---:|---:|---:|---|---|']
    def fmt(n):return f'{Decimal(n):,.4f}' if n is not None else '미확인'
    for r in v['relative_valuation']:
        difference=fmt(r['yield_spread_percentage_points'])+'%p' if r['metric_name']=='FCF_YIELD' else fmt(r['premium_discount_percent'])+'%'
        lines.append(f'| {r["metric_name"]} | {fmt(r["target_value"])} | {fmt(r["peer_median"])} | {difference} | {r["eligible_peer_count"]}/{r["minimum_peers"]} | {r["output_status"]} |')
        for p in r['included']:lines.append(f'- {r["metric_name"]} 비교 관측: {p["ticker"]} {fmt(p["value"])}. 단일 기업은 산업 평균·중앙값으로 부르지 않습니다.')
        for p in r['excluded']:lines.append(f'- {r["metric_name"]} / {p["ticker"]}: '+safe(', '.join(p['reasons'])))
    lines+=['관측 배수의 할증·할인은 고평가·저평가의 확정 판정이 아닙니다. 일회성 손익은 독립 회계 검토되지 않았고 비교군의 사업 구성과 재무 종료일이 다를 수 있습니다.',
        '','## 11. Historical Valuation']
    h=v['historical_valuation'];lines += [f'상태: {h["classification"]}. 캐시 가격 범위: {h["available_price_start"]} ~ {h["available_price_end"]} ({h["cached_price_sessions"]}세션).',
        '그 당시의 재무자료·시가총액·주식 수 vintage가 부족하므로 역사 percentile은 미산출입니다. 현재 취득한 과거 bar를 완전한 PIT 밸류에이션으로 표시하지 않습니다.',
        '','## 12. Quantitative Bull/Base/Bear']
    assumptions={r['scenario_name']:r for r in v['scenario_assumptions']}
    if not v['scenario_results']:lines.append('시나리오 보류: '+safe('; '.join(v['scenario_limitations'])))
    if v['scenario_results']:
        ref=v['scenario_reference'];lines += [f'기준 재무기간: {ref["reference_period"]["start"]} ~ {ref["reference_period"]["end"]} / {ref["reference_period_classification"]} / {ref.get("reference_period_days")}일.',
            f'명목 연도 단계 {v["scenario_horizon"]}년. 53주 실적의 규모를 숨겨서52주로 보정하지 않습니다. 종료 시점의 조건부 가치이며 현재가치 할인·배당·거래비용은 포함하지 않습니다.',
            '', '| 시나리오 | 매출 성장 가정 | 순이익률 가정 | 방법/종료 배수 가정 | 조건부 지분가치 백만 USD | 기준 시총 대비 변화 | 조건부 주당 가치 |',
            '|---|---:|---:|---|---:|---:|---:|']
        for r in v['scenario_results']:
            a=assumptions[r['scenario_name']]['fields'];pct=lambda k:f'{Decimal(a[k]["assumption_value"])*100:,.2f}%' if k in a else '미확인'
            for out in r['valuations']:
                key='exit_pe' if out['method']=='EARNINGS' else 'exit_ps'
                amount=f'{Decimal(out["equity_value"])/1000000:,.2f}' if out['equity_value'] is not None else '미산출'
                lines.append(f'| {r["scenario_name"].upper()} | {pct("growth")} | {pct("net_margin")} | {out["method"]} / {fmt(a[key]["assumption_value"]) if key in a else "미확인"} | {amount} | {fmt(out["change_vs_reference_market_cap_percent"])}% | {fmt(out["per_share_value"])} |')
            lines.append(f'- {r["scenario_name"]}: 예상 매출 {Decimal(r["projected_revenue"])/1000000:,.2f}백만 USD; 순이익 {fmt(r["projected_net_income"])} USD; 영업손익 {fmt(r["projected_operating_income"])} USD; FCF {fmt(r["projected_fcf"])} USD.')
        lines+=['미래 마진·성장·배수는 ILLUSTRATIVE_ASSUMPTION 또는 USER_ASSUMPTION입니다. 실제 가이던스를 새로 확인한 것으로 표시하지 않습니다. 관측된 이익률을 미래에 유지하는 것 자체도 가정입니다.',
            'MU 기본 시나리오의 정상화는 현재 순이익률의50%를 쓰는 연구 가정이며 역사적 평균이 아닙니다. FCF에는 CAPEX·운전자본의 영향을 자동 예측하는 모형이 없습니다.',
            '주식 class·분할·미래 희석 기준이 미완결이면 주당 가치는 미산출입니다. EPS 가중평균 주식 수나 단일 class를 전체 지분가치의 분모로 쓰지 않습니다.']
        for name,a in assumptions.items():
            lines.append('- '+name+' 가정 근거: '+safe(a['policy']))
            for key,f in a['fields'].items():lines.append(f'  {key}: {f["assumption_type"]}; {safe(f["source_or_derivation"])}')
    lines+=['','## 13. Sensitivity Analysis']
    for grid in v['sensitivity_results']:
        if not grid.get('grid'):lines.append(grid['method']+': 입력 부족');continue
        lines += [f'{grid["method"]}: 행={grid["row_variable"]}, 열={grid["column_variable"]}. 값은 조건부 지분가치 백만 USD.',
            '', '| 가정 | '+' | '.join(fmt(x) for x in grid['column_values'])+' |','|---|'+'---:|'*len(grid['column_values'])]
        for label,row in zip(grid['row_values'],grid['grid']):lines.append('| '+fmt(label)+' | '+' | '.join(f'{Decimal(x)/1000000:,.2f}' if x is not None else '미확인' for x in row)+' |')
        lines.append('지정 범위 안에서 더 큰 가치 변화 요인: '+str(grid.get('largest_declared_range_effect'))+'. 실제 변동성·확률 분포·일반적인 인과 효과가 아닙니다.')
    lines+=['','## 14. Investment Thesis & Counter-Thesis',sections['Investment Thesis'],sections['Risks & Counter-Thesis']]
    for driver in v['valuation_drivers']:lines.append(f'- {driver["business_driver"]} → {driver["related_financial_metric"]} 가정 → {driver["valuation_effect"]}. 반론: {driver["counter_thesis"]} {driver["uncertainty"]}')
    lines+=['','## 15. Key Risks & Monitoring Indicators']
    lines += ['- '+x['indicator']+': '+x['purpose'] for x in v['monitoring_indicators']]
    lines += [sections['Recent Events'],'','기존23요인·가중치·ResearchEngine SHADOW는 그대로입니다. 조건부 가치가 상승해도 투자 점수를 만들거나 매수 신호로 전환하지 않습니다.',
        '','## 16. Sources & Assumptions',sections['Sources & Uncertainty']]
    lines += ['- '+x for x in v['scenario_limitations']]
    return '\n'.join(lines)+'\n'


def render(v):
    text=_render_v4(v)
    if 'investment_memo' in v:text+='\n[P5 Investment Memo](MEMO.ko.md) · synthesis_mode='+v['synthesis_mode']+'\n'
    if 'research_pipeline' in v:
        text=text.replace('신규 모델·유료 데이터·목표가·매매 신호는 사용하지 않았습니다.','Qwen3.5의 실제 실행 여부와 검증 상태는 별도 AI Research Memo에 표시합니다. 유료 데이터·목표가·매매 신호는 추가하지 않았습니다.')
        text+='\n[P5.4 AI Research Memo](RESEARCH_MEMO.ko.md) · '+v['research_pipeline']['research_state']+' / Verified: '+v['research_pipeline']['verified_state']+'\n'
    return text


def write(value, snapshot, output=None):
    core.validate(value)
    root = Path(output or ROOT / 'reports/private/personal_research').resolve()
    if not root.is_relative_to((ROOT / 'reports/private').resolve()): raise ValueError('PRIVATE_PERSONAL_OUTPUT_REQUIRED')
    folder = root / uuid.uuid4().hex; folder.mkdir(parents=True, exist_ok=False)
    atomic_json(folder / 'RESULT.json', value); atomic_json(folder / 'INPUT.json', snapshot)
    with (folder / 'REPORT.ko.md').open('x', encoding='utf-8') as f: f.write(render(value))
    names=['RESULT.json','INPUT.json','REPORT.ko.md']
    if 'investment_memo' in value:
        from . import memo
        with (folder/'MEMO.ko.md').open('x',encoding='utf-8') as f:f.write(memo.render(value))
        names.append('MEMO.ko.md')
    if 'research_pipeline' in value:
        from . import research_output
        for name,payload in research_output.outputs(value).items():atomic_json(folder/name,payload);names.append(name)
        with (folder/'RESEARCH_MEMO.ko.md').open('x',encoding='utf-8') as f:f.write(research_output.render(value))
        names.append('RESEARCH_MEMO.ko.md')
    atomic_json(folder/'FILES.json',{n:digest(folder/n) for n in names})
    return folder


def validate(folder):
    folder = Path(folder); value = read(folder/'RESULT.json'); snapshot = read(folder/'INPUT.json')
    if value['contract']=='personal-research-v5.4':
        from .v54 import report as original
        return original.validate(folder)
    if value['contract']=='personal-research-v5.1-r1':
        from .v51r1 import report as original
        return original.validate(folder)
    if value['contract']=='personal-research-v1':
        from .v1 import report as original
        return original.validate(folder)
    if value['contract']=='personal-research-v2':
        from .v2 import report as original
        return original.validate(folder)
    if value['contract']=='personal-research-v3':
        from .v3 import report as original
        return original.validate(folder)
    if value['contract']=='personal-research-v5':
        from .v5 import report as original
        return original.validate(folder)
    if value['contract']=='personal-research-v5.1':
        from .v51 import report as original
        return original.validate(folder)
    if value['contract']=='personal-research-v4':
        from .v4 import report as original
        return original.validate(folder)
    if any(digest(folder/n) != h for n,h in read(folder/'FILES.json').items()): raise ValueError('PERSONAL_REPORT_BYTES_CHANGED')
    if value['provenance']['method_hashes'] != methods(): raise ValueError('PERSONAL_METHOD_CHANGED')
    core.validate(value)
    if 'research_pipeline' in value:
        from . import adoption,research_output
        rebuilt=adoption.rebuild(value,snapshot)
        if rebuilt!=value:raise ValueError('P54_REPLAY_CHANGED')
        for name,payload in research_output.outputs(value).items():
            if read(folder/name)!=payload:raise ValueError('P54_SIDECAR_CHANGED')
        if (folder/'RESEARCH_MEMO.ko.md').read_text(encoding='utf-8')!=research_output.render(value):raise ValueError('P54_RESEARCH_RENDER_CHANGED')
    else:rebuilt, _ = core.build(value['ticker'], value['analysis_as_of'], snapshot=snapshot, illustrative=value['is_illustrative'],
                           market_snapshot=value['market_snapshot'],sec_acquisition=value.get('sec_acquisition'),scenario_horizon=value.get('scenario_horizon',1),scenario_assumptions=value.get('scenario_override_inputs'),peer_inputs=value.get('peer_inputs',[]),ai=value.get('ai_requested',False),ai_dry_run=value.get('ai_dry_run',False),ai_record=value.get('ai_record'),ai_mode=value.get('ai_mode','roles'),ai_context=value.get('ai_context','primary'))
    if rebuilt != value: raise ValueError('PERSONAL_REPLAY_CHANGED')
    if render(value) != (folder/'REPORT.ko.md').read_text(encoding='utf-8'): raise ValueError('PERSONAL_RENDER_CHANGED')
    if 'investment_memo' in value:
        from . import memo
        if memo.render(value)!=(folder/'MEMO.ko.md').read_text(encoding='utf-8'):raise ValueError('MEMO_RENDER_CHANGED')
    return {'status': 'VALID', 'ticker': value['ticker'], 'analysis_status': value['analysis_status'], 'network_requests': 0}
